# Management subscription monitoring baseline

`environments.platform.example.org/v1alpha1 XManagementSubscription` is a
namespaced environment API for an **existing** Azure subscription. It follows
`XWebApplication`: kro composes resource-level child XRs, not an inline ARM
deployment. Governance remains independently owned by `XLandingZone`.

## Contract

Required inputs: immutable DNS-safe `spec.name` (1–30 characters),
immutable existing `spec.subscriptionId` UUID, and immutable `spec.location`.
Names must be unique within the namespace, including the derived child names.

Choose exactly one target form:

```yaml
---
apiVersion: environments.platform.example.org/v1alpha1
kind: XManagementSubscription
metadata:
  name: operations
  namespace: platform
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  name: operations
  subscriptionId: 11111111-2222-3333-4444-555555555555
  location: swedencentral
  landingZoneRef:
    name: enterprise
  managementGroupRef:
    name: management
```

Or replace both reference objects with:

```yaml
---
managementGroupId: /providers/Microsoft.Management/managementGroups/existing
```

The latter is an existing external group, never adopted or deleted.
`landingZoneRef` resolves in the XR namespace; its
`status.managementGroups[managementGroupRef.name].id` must be observed.
`managementGroupRef.name` defaults to `management`. The landing zone need not
be overall Ready, avoiding a governance/monitoring readiness cycle.
The reference mode and landing-zone name cannot change in place.

Optional inputs:

| Field | Default |
| --- | --- |
| `providerConfigRef.kind` | `ClusterProviderConfig` |
| `providerConfigRef.name` | `default` |
| `retentionInDays` | 30 (range 30–730) |
| `dailyQuotaGb` | -1/unlimited (minimum -1) |

The source provider reference is immutable. A namespaced source ProviderConfig
is resolved in the same namespace. The composition copies its entire **spec**
(tenant, cloud, auth settings and credential Secret reference), overrides
`subscriptionID`, and creates the namespaced
`ProviderConfig/<spec.name>-management`. It never reads/copies Secret data,
creates credentials, or falls back to the bootstrap subscription after failure.
Credential authentication and imported-subscription access are verified by the
provider when reconciling the subscription association and resources.

All subscription-scoped child XRs receive that generated ProviderConfig.
Do not delete or edit it independently. Missing source/landing-zone references
fail reconciliation rather than silently pruning an existing baseline.
Existing child resources are retained while a deleted target ProviderConfig is
being recreated; the environment remains not Ready.

## Resources and outputs

The baseline owns:

1. `XSubscription`: only a management-group association, never the subscription.
2. `XResourceGroup`: `rg-<name>-monitoring`.
3. `XLogAnalyticsWorkspace`: `<name>-workspace`, shared-key auth disabled.
4. `XUserAssignedIdentity`: `<name>-ama`, without role assignments.
5. Three `XDataCollectionRule`s: `vmInsights`, `changeTracking`, `defenderSql`.
6. `XDiagnosticSetting`: the subscription's eight activity-log categories.

`status` exposes observed `id` (existing subscription ARM ID),
`associationId`, `managementGroupId`, `resourceGroupId`, `workspaceId`,
`identity.{id,clientId,principalId}`,
`dataCollectionRuleIds.{vmInsights,changeTracking,defenderSql}`,
`diagnosticSettingId`, and the generated `providerConfigRef`.
There are no keys, tokens or passwords in status.

No AMA or extension installation, workload/DCR associations, Defender service
configuration, Sentinel, Automation, connectivity, alerts or Azure RBAC grants
are included. A Defender SQL DCR **does not enable Defender**.
No deployIfNotExists policy or remediation is involved.

Exactly one composition may own the management subscription attachment:
do not also put the UUID in `XLandingZone.spec.subscriptions`.

## Readiness and deletion

`function-sequencer:v0.6.1` uses `enableDeletionSequencing: true`,
`usageVersion: v2`, `replayDeletion: true`, and
`resetCompositeReadiness: true`. Child nodes have explicit `readyWhen`
conditions, so the sequencer can release successors before auto-ready runs.
A final explicit gate remains False until every expected child, observed ID,
correct ProviderConfig target, and all 22 nested Usage protections are ready.
It prevents partial graphs from reporting Ready.

The sibling creation order is ProviderConfig → subscription attachment →
resource group → workspace/identity → DCRs/diagnostics.
Teardown reverses the dependencies.

Sibling XR sequencing alone is insufficient: a child XR may disappear before
its finalizing Azure MR. Additional Usages refer to **actual nested MR names**
reported by each child:

- Every nested MR protects the target ProviderConfig.
- The resource-group MR protects the subscription-attachment XR.
- Workspace/identity MRs protect the resource-group XR.
- DCR MRs protect the workspace, identity and resource-group XRs.
- The diagnostic MR protects the workspace and resource-group XRs.

Usage finalizers retain those edges until the actual MRs have disappeared,
even if their owning child XRs have already gone.
A referenced external landing zone is protected by a cross-XR Usage whose
`by` is the generated ProviderConfig, the last-lived baseline resource.
Using the owning management XR as `by` for one of its own composed Usages
would create a foreground-GC cycle. There is no ownership of the landing zone.

**Supported teardown requires foreground cascading deletion**, with Usages
intact and the baseline Ready first. Ordinary background deletion, independently
deleting child XRs, and unverified Flux pruning are not supported workflows.
Remove/suspend the GitOps manifest first so reconciliation does not recreate it:

```sh
kubectl delete xmanagementsubscriptions.environments.platform.example.org \
  operations -n platform --cascade=foreground --wait=true --timeout=30m
# Only after management cleanup, if this landing zone is also owned:
kubectl delete xlandingzones.azure.platform.example.org \
  enterprise -n platform --cascade=foreground --wait=true --timeout=30m
```

The example's `kustomize.toolkit.fluxcd.io/prune: disabled` retains the XR
during ordinary Flux prune. It does not impose Azure deletion order.
Do not remove Usage finalizers or force deletion to bypass an Azure error.
No live Azure teardown has been verified; tests check generated dependency
graphs and real function execution against synthetic observed resources.

## Installation requirements

Providers stay pinned at **v2.7.2**. Exact package schemas were extracted and
the normalized source fixtures plus extracted-package SHA-256s are committed
under each primitive's `tests/`; no v2.7.0 schema fallback was needed.

Required MRDs:

```text
resourcegroups.azure.m.upbound.io
managementgroupsubscriptionassociations.management.azure.m.upbound.io
workspaces.operationalinsights.azure.m.upbound.io
userassignedidentities.managedidentity.azure.m.upbound.io
monitordatacollectionrules.insights.azure.m.upbound.io
monitordiagnosticsettings.insights.azure.m.upbound.io
```

Packages: existing Azure family/core plus `provider-azure-management`,
`provider-azure-operationalinsights`, `provider-azure-managedidentity`,
`provider-azure-insights`. Functions: existing patch-and-transform, kro,
go-templating, auto-ready, plus sequencer v0.6.1.
Crossplane v2 protection `Usage` CRDs/controllers/webhook must be enabled.

The Crossplane composition controller needs:

- Read access to `azure.m.upbound.io` ClusterProviderConfigs and same-namespace
  ProviderConfigs; full compose/manage access to namespaced ProviderConfigs.
- Read access to same-namespace `azure.platform.example.org` XLandingZones.
- Compose/manage access to the child XR kinds, core ConfigMaps, and
  `protection.crossplane.io` Usages (including finalizers/owner references).
- Existing managed-resource and required-schema discovery permissions.

Functions need no direct Secret read grant for this copying step.
The existing credential principal needs access to the imported subscription
for monitoring resources and Azure's subscription-to-management-group move
permissions on the subscription/current/target groups. Bootstrap's default
subscription Contributor does not imply those management-group permissions.
No permission escalation is performed.

## Validation

```sh
python3 -m unittest discover -s compositions/tests \
  -p test_managementsubscription_contract.py -v
```

`tests/test_runtime.py` additionally runs the **real pinned functions** when
`MANAGEMENT_FUNCTION_ENDPOINTS` contains a JSON function-name-to-local-gRPC
endpoint map and the Crossplane Python SDK is available. It exercises all DCR
profiles, pending/observed orchestration, whole-spec credential-reference
copying, partial landing-zone readiness, nested Usage generation, and the final
all-expected-resources readiness gate. No Docker or Kubernetes client is used
by the test itself; runtimes must already be running.
