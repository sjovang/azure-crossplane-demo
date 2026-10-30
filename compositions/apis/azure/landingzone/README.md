# Azure landing zone governance

`XLandingZoneArchitecture` deploys management groups and an **audit/deny-only projection**
of the built-in Azure landing zones archetypes. It is not the complete,
unmodified ALZ policy baseline.

The composition owns management groups, ALZ-derived policy definitions,
filtered initiatives, policy assignments, and optional existing-subscription
attachments. It does not create subscriptions, custom roles, role assignments,
policy identities, or remediation tasks.

## Default hierarchy

```yaml
---
apiVersion: azure.platform.example.org/v1alpha1
kind: XLandingZoneArchitecture
metadata:
  name: contoso
  namespace: platform
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  name: contoso
  parentManagementGroupId: /providers/Microsoft.Management/managementGroups/tenant-id
```

Omitting `managementGroups` selects the pinned library's complete default
topology, including its Local and Security groups. Azure names are `contoso`
for the ALZ root and `contoso-<logical-name>` for its descendants.
The existing parent is never owned.

Instead of committing an environment-specific parent ID, reference an externally
managed, cluster-scoped `EnvironmentConfig`:

```yaml
---
apiVersion: azure.platform.example.org/v1alpha1
kind: XLandingZoneArchitecture
metadata:
  name: example
  namespace: platform
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  name: example
  environmentConfigRef:
    name: azure-platform
```

Supply exactly one of `parentManagementGroupId` and `environmentConfigRef`.
The referenced configuration must contain a valid management-group ARM ID in
`data.parentManagementGroupId`. Provision it locally after Crossplane is
installed, not in the public GitOps manifests; the
[`alz-architecture` setup](../../../../teams/alz-architecture/README.md)
derives the tenant root from bootstrap's local platform configuration.
Missing/invalid data fails reconciliation without fallback or pruning.
Neither the source nor the resolved parent may change once the ALZ root exists.
The composition reads the EnvironmentConfig but never owns or deletes it.

The bootstrap identity needs management-group hierarchy/policy permissions
below this parent and access to each imported subscription. Contributor on the
bootstrap subscription alone is insufficient. Assign appropriate permissions
explicitly; these compositions do not grant themselves tenant-wide access.

## Custom hierarchy and imported subscriptions

```yaml
---
apiVersion: azure.platform.example.org/v1alpha1
kind: XLandingZoneArchitecture
metadata:
  name: contoso
  namespace: platform
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  name: contoso
  parentManagementGroupId: /providers/Microsoft.Management/managementGroups/tenant-id
  managementGroups:
    - name: root
      displayName: Contoso
      archetypes: [root]
    - name: management
      displayName: Management
      parentRef:
        name: root
      archetypes: [management]
    - name: applications
      displayName: Applications
      parentRef:
        name: root
      archetypes: [landing-zones]
  subscriptions:
    - name: application-production
      subscriptionId: 11111111-1111-4111-8111-111111111111
      managementGroupRef:
        name: applications
```

Public fields are camelCase and hierarchy/archetype names are DNS-safe.
The importer translates internal ALZ names such as `landing_zones`.
Every group must connect to exactly one root, determined by the absence of
`parentRef`. The `root` archetype is optional but may only be selected on that
top-level group; other supported archetypes may also be selected there.
For explicitly supplied groups, omitted `archetypes` defaults to `[]`.
An empty selection creates the group without assigning policies to it.
Omitting the entire `managementGroups` field still selects the original default
topology and archetypes.

Parent references are logical names within this list. An empty group list, unknown
archetypes, cycles, duplicate names/subscriptions, and unknown targets are errors.
Account for the existing parent's depth within Azure's six-level limit.
The API supports up to 100 groups and 100 subscription attachments per XR.

Reordering, adding groups, and updating display names/archetype selections are
supported. Removing or reparenting existing groups is rejected in v1; use an
explicit teardown rather than an implicit destructive replacement.
Group names and the tenant-wide prefix are identity, not display settings.
Changing archetype selections can remove previously selected composition-owned
policy assignments, initiatives, and definitions that are no longer needed.
It does not disable policies inherited from ancestor management groups.

The [`alz-architecture` example](../../../../teams/alz-architecture/README.md)
declares the complete 12-group topology with empty archetypes on every group.
It creates no policy assets and needs no `policyDefaults`, while retaining
hierarchy readiness checks and foreground deletion protections.

Only one XR may own a subscription attachment. Do not list the management
subscription here if `XManagementSubscription` owns it. A subscription referenced
by an association remains an existing subscription; deleting the association
detaches it but never cancels or deletes it.
The attachment's logical `name` is passed to the child `XSubscription`; the
landing-zone prefix is used only for that child's Kubernetes metadata name.

## Policy effects and required values

`profile.json` records every retained/converted/omitted member and the inputs
needed by each assignment. Only effective `Audit`, `AuditIfNotExists`, and
`Deny` effects are permitted. Policies without a supported audit alternative
are omitted, including hardcoded DINE/Modify/Append/DenyAction policies.
Mixed initiatives become distinctly named root-scoped filtered variants.
Microsoft built-in definitions remain untouched; references are pinned to the
reviewed snapshot's `major.minor.*` version (Azure's reference format; a
`-deprecated` metadata label is dropped, `-preview` is kept). Custom
definitions hardcode the selected effect and drop parameters the rule no
longer uses, which Azure rejects.
Upstream `DoNotEnforce` assignments remain non-enforcing; filtering never
promotes them into enforced denies. Supported non-compliance messages are kept.
Supported assignment-level `policyEffect` overrides are retained for `Audit`,
`AuditIfNotExists`, and `Deny`; unsupported override kinds or effects fail
generation rather than silently changing policy behavior.

Azure requires a managed identity on any assignment whose initiative contains
a built-in with a deployment in its effect details, even when the selected
effect is audit-only (`ResourceIdentityRequired`). Those assignments get a
system-assigned identity in `spec.location` (default `swedencentral`,
immutable). No roles are granted, so the identity cannot remediate.

The pinned built-in snapshot includes newer Kubernetes policy versions than
some ALZ references. Unsupported deny effects become a supported audit effect;
the host-port policy's old bounds are migrated to its version-8 range input.
These reviewed adaptations are visible in the profile report. Generation fails
on unknown effects, unsupported parameter expressions, unresolved references,
or unmapped deployment placeholders.

For the complete default archetype selection, supply the relevant existing
resource IDs/settings through `spec.policyDefaults`:

```yaml
policyDefaults:
  logAnalyticsWorkspaceId: /subscriptions/.../providers/Microsoft.OperationalInsights/workspaces/...
  amaUserAssignedManagedIdentityId: /subscriptions/.../providers/Microsoft.ManagedIdentity/userAssignedIdentities/...
  ddosProtectionPlanId: /subscriptions/.../providers/Microsoft.Network/ddosProtectionPlans/...
  resourceGroupLocation: swedencentral
  resourceGroupNameServiceHealthAlerts: rg-service-health
```

Only selected assignments consume these fields. Missing values keep those
assignments pending and `PoliciesReady=False`; management groups and unrelated
assignments can still reconcile. These are policy evaluation inputs, not an
instruction to deploy DDoS, identities, or alert resources.
The management-subscription composition exposes workspace/identity outputs;
DDoS remains external connectivity configuration.

Get role-assignment scopes from observed status, not reconstructed strings:

```sh
kubectl -n platform get xlandingzonearchitecture contoso \
  -o 'jsonpath={.status.managementGroups.management.id}'
```

`status.id` is the observed ALZ-root ID. `ManagementGroupsReady`,
`PoliciesReady`, and `SubscriptionsReady` distinguish partial progress.
IDs become available before all policy assignments are ready.

## Foreground teardown

Use `function-sequencer` and Crossplane v2 Usages with explicit foreground
deletion. Dynamic Usages protect each child/parent and assignment/target-group
edge; sequencer rules protect definition and initiative dependencies.
Subscription Usages reference the nested association MR directly and preserve
observed protection after its child XR disappears, until Usage finalizers finish.

First remove the management environment, then the landing zone:

```sh
kubectl -n platform delete xmanagementsubscription management --cascade=foreground
kubectl -n platform delete xlandingzonearchitecture contoso --cascade=foreground
```

Stop reconciliation of their manifests before teardown. The examples disable
Flux pruning on these XRs because ordinary background pruning does not preserve
the sequencer contract. Do not remove Usage protections to force deletion.
External child groups/subscription attachments must be handled by their owners;
Azure rejects deletion of nonempty groups rather than deleting unrelated assets.
Management-subscription teardown deletes its owned monitoring resources, which
can include stored workspace data, but never deletes the subscription.

## Reproducible generation and tests

`library-lock.json` pins upstream commits and the snapshot hash. `library.json`
contains only the transitive assets needed by built-in archetype assignments.
The two `LICENSE-*` files retain upstream licensing.

```sh
# Explicit, reviewed source refresh; requires gh and network access.
python3 compositions/apis/azure/landingzone/update_library.py
# Offline generation and drift detection.
make -C compositions generate
python3 compositions/apis/azure/landingzone/generate.py --check
python3 -m unittest discover -s compositions/apis/azure/landingzone/tests
```

Optional real-function tests use `crossplane-function-sdk-python` (Python 3.11+)
and PyYAML. Run the exact pinned functions with `--insecure` on localhost ports
19443 (go-templating), 19444 (kro), 19445 (sequencer), and 19446 (auto-ready).
Apple `container run --publish 127.0.0.1:19443:9443
ghcr.io/crossplane-contrib/function-go-templating:v0.12.4 --insecure` is suitable;
use the corresponding pinned package/version for the other ports.

Extract the authorization/management provider packages with
`crossplane xpkg extract ... -o <package>.gz`, then set
`ALZ_RENDER_SCHEMAS=<authorization.gz>:<management.gz>` and run those tests
with the SDK-enabled Python. The tests use synthetic observed resources only.
They verify function evaluation, stable identities, staged readiness, and Usage
edges; they do not prove live Azure teardown. An explicitly authorized
non-production lifecycle check remains necessary before a production rollout.
