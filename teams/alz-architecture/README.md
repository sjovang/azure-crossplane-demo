# ALZ architecture

This GitOps example creates the pinned ALZ topology of 12 management groups in
namespace `alz-architecture`, using
[`XLandingZoneArchitecture`](../../compositions/apis/azure/landingzone/README.md).
Every group explicitly selects `archetypes: []`: no composition-owned policy
definitions, initiatives, or assignments are created, and no policy inputs are
needed. Azure policies inherited from the tenant root still apply.

The existing tenant-root parent is
`/providers/Microsoft.Management/managementGroups/414815d3-f2c3-461d-8982-7c57e77a660c`.
The ALZ root's Azure name is `alz-architecture`; descendants use
`alz-architecture-<logical-name>`. These names are tenant-wide: the Kubernetes
namespace does not isolate them. Confirm the prefix is unused before rollout.
The existing parent is never owned or deleted.

## Deployment

The shared `teams` Flux Kustomization picks up this folder after changes reach
the branch Flux watches. It waits for the shared composition CRDs. The default
`ClusterProviderConfig/default` must use an identity authorized to create and
manage groups below the tenant root; subscription Contributor alone is
insufficient. Grant permissions explicitly, not through this manifest.

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
