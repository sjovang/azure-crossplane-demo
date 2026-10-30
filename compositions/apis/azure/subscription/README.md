# Existing subscription attachment

`XSubscription` owns **only** a namespaced
`management.azure.m.upbound.io/v1beta1 ManagementGroupSubscriptionAssociation`.
It never creates or deletes a subscription.

- `spec.name`: immutable DNS-safe logical name, up to 40 characters.
- `spec.subscriptionId`: immutable existing subscription UUID.
- `spec.managementGroupId`: full target management-group ARM ID.
- `spec.providerConfigRef`: `{kind, name}`, defaults to
  `ClusterProviderConfig/default`; `ProviderConfig` is same-namespace.
- The input UUID is formatted as `/subscriptions/<uuid>` for the provider's
  association resource. `status.id` copies the provider's observed subscription
  ARM ID unchanged; `status.associationId` is the observed association ID.
- `status.managedResourceName`: observed Kubernetes MR name for nested
  cleanup protection.

Exactly one XR may own each subscription attachment. Do not also list this
subscription under `XLandingZoneArchitecture.spec.subscriptions`.

The principal needs permission to move the existing subscription under the
target group, including Azure's current/target-parent and subscription move
prerequisites. Subscription-scoped Contributor alone is not sufficient.
Deletion detaches the association; Azure controls the resulting parent placement.
It does not delete the subscription or resources in it.

The package/schema fixture in `tests/` is extracted from the exact pinned
provider v2.7.2, not inferred from Azure ARM hierarchy.
