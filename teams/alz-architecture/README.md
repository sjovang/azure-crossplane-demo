# ALZ architecture

This GitOps example creates the pinned ALZ topology of 12 management groups in
namespace `alz-architecture`, using
[`XLandingZoneArchitecture`](../../compositions/apis/azure/landingzone/README.md).
Every group explicitly selects `archetypes: []`: no composition-owned policy
definitions, initiatives, or assignments are created, and no policy inputs are
needed. Azure policies inherited from the tenant root still apply.

The existing parent is read from the cluster-scoped `EnvironmentConfig`
`azure-platform`, at `data.parentManagementGroupId`. No tenant ID is stored in
this team's manifests.
The ALZ root's Azure name is `alz-architecture`; descendants use
`alz-architecture-<logical-name>`. These names are tenant-wide: the Kubernetes
namespace does not isolate them. Confirm the prefix is unused before rollout.
The existing parent is never owned or deleted.

## Deployment

After Crossplane is installed, create the EnvironmentConfig locally from the
bootstrap-created `azure-platform-config` ConfigMap. This uses the provider's
tenant rather than whichever Azure account happens to be selected:

```sh
tenant_id="$(kubectl -n crossplane-system get configmap azure-platform-config \
  -o 'jsonpath={.data.tenantId}')" &&
test -n "$tenant_id" &&
jq -n --arg parent "/providers/Microsoft.Management/managementGroups/$tenant_id" \
  '{apiVersion: "apiextensions.crossplane.io/v1beta1",
    kind: "EnvironmentConfig", metadata: {name: "azure-platform"},
    data: {parentManagementGroupId: $parent}}' |
  kubectl apply -f -
```

Keep this cluster-specific configuration out of the public repository. An
existing non-root parent can be supplied in the same data field instead.
Missing or invalid configuration reports a reconciliation error; it never
falls back to another parent. Changing the parent after the ALZ root exists is
rejected. The EnvironmentConfig is externally managed and is not deleted with
the landing zone.

The shared `teams` Flux Kustomization picks up this folder after changes reach
the branch Flux watches. It waits for the shared composition CRDs. The default
`ClusterProviderConfig/default` must use an identity authorized to create and
manage groups below the tenant root; subscription Contributor alone is
insufficient. Both bootstrap scripts now create/assign the custom
`azure-crossplane-demo ALZ Management Groups` role at the tenant root derived
from the credentials file, including for a reused service principal.
Its only actions are `Microsoft.Management/managementGroups/read`, `write`, and
`delete`. It grants neither policy administration, subscription moves, nor RBAC
administration at the tenant root. Deletion is needed for foreground teardown.

Root-scoped permissions inherit across the tenant hierarchy, not just groups
with this demo's prefix; use an isolated demo tenant. Azure RBAC does not
restrict these actions by the management-group name prefix.
The existing subscription-scoped Contributor/RBAC Administrator and Graph
permissions remain unchanged; this is least privilege for the **additional
tenant-root grant**, not for the demo identity as a whole.

The bootstrap operator needs `Microsoft.Authorization/roleDefinitions/write`
and `Microsoft.Authorization/roleAssignments/write` at that management-group
scope. No automatic elevation is attempted. If bootstrap warns about missing
rights, an authorized administrator can retry the shared helper without
recreating the cluster or credentials:

```sh
source infrastructure/lib/common.sh
ensure_alz_management_group_permissions infrastructure/azure-credentials-kiac.json
# For AKS, use infrastructure/azure-credentials-aks.json instead.
```

An existing role with broader/different permissions is rejected, not silently
reused or overwritten. These rights cover this policy-free example only;
enabling policy archetypes or subscription attachments needs separate grants.

No subscriptions are created or moved. No networking, monitoring resources,
identities, or role assignments are provisioned. To add policies later, select
supported archetypes on individual groups and supply any required
`policyDefaults`; missing inputs leave the corresponding policies pending.
The `root` archetype is optional and can only be selected on the top-level group.
Removing a selection can delete its composition-owned policy assets.

Inspect observed IDs and readiness after Flux reconciliation:

```sh
kubectl -n alz-architecture get xlandingzonearchitecture alz-architecture
kubectl -n alz-architecture get xlandingzonearchitecture alz-architecture \
  -o 'jsonpath={.status.id}{"\n"}{.status.conditions}{"\n"}'
```

`ManagementGroupsReady` requires observed Azure IDs and ready managed resources.
`PoliciesReady` and `SubscriptionsReady` have no selected resources to wait for
in this example; hierarchy Usage protections must still become ready.

## Teardown

Flux pruning is disabled on the XR to preserve foreground deletion ordering.
First remove `landingzone.yaml` from this team's Kustomization and publish that
change; keep the namespace registered until cleanup finishes. Then delete:

```sh
kubectl -n alz-architecture delete xlandingzonearchitecture alz-architecture \
  --cascade=foreground
```

Do not remove Usage protections to force deletion. External groups or
subscription attachments must be handled by their owners before Azure permits
group deletion. Remove the team registration only after cleanup is complete.
