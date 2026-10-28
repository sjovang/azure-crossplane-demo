# Log Analytics workspace

`XLogAnalyticsWorkspace` composes a namespaced
`operationalinsights.azure.m.upbound.io/v1beta1 Workspace`.

Inputs: immutable `name` (4–63 DNS-safe characters), `resourceGroupName`,
`location`; `retentionInDays` (30–730, default 30);
`dailyQuotaGb` (default -1, unlimited; minimum -1);
optional `providerConfigRef` (default `ClusterProviderConfig/default`).

The SKU is `PerGB2018`, local/shared-key authentication is disabled, and public
ingestion/query are enabled. Private networking is outside this API.
Outputs are observed `status.id`, `status.workspaceId`, and
`status.managedResourceName`. No workspace keys are published.

When used independently, protect the resource group and any external workspace
consumers with Usages. The management orchestration supplies these lifetime edges.
Exact provider v2.7.2 schema is recorded under `tests/`.
