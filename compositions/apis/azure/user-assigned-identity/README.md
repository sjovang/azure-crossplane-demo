# Monitoring identity

`XUserAssignedIdentity` composes a namespaced
`managedidentity.azure.m.upbound.io/v1beta1 UserAssignedIdentity`.

Inputs: immutable `name` (3–63 DNS-safe characters), `resourceGroupName`,
`location`; optional `providerConfigRef`, default
`ClusterProviderConfig/default` (or same-namespace `ProviderConfig`).

Outputs: observed `status.id`, `status.clientId`, `status.principalId`,
and `status.managedResourceName`. No credential is created or published.
Creating the identity does not grant Azure permissions or install AMA.

Independent consumers must protect the identity's lifetime with Usages.
The management orchestration supplies concrete-MR lifetime edges.
Exact provider v2.7.2 schema is recorded under `tests/`.
