# Monitoring data collection rule

`XDataCollectionRule` composes one namespaced
`insights.azure.m.upbound.io/v1beta1 MonitorDataCollectionRule` using kro.

Inputs:

- Immutable DNS-safe `name`, `resourceGroupName`, `location`, and `profile`.
- `workspaceId`: full Log Analytics workspace ARM ID.
- `identityId`: full user-assigned managed-identity ARM ID.
- Optional `providerConfigRef`, default `ClusterProviderConfig/default`.

Profiles:

| Profile | Collection configured |
| --- | --- |
| `vmInsights` | VM Insights detailed performance counters every 60 seconds, `Microsoft-InsightsMetrics`. No Dependency Agent/Service Map. |
| `changeTracking` | Windows/Linux Change Tracking extension sources, configuration-change and inventory streams. |
| `defenderSql` | The five Microsoft Defender for SQL collection streams; SQL-query collection for security research is disabled. |

These are collection rules, **not** AMA installation, machine associations,
Change Tracking extension installation, or Defender enablement. No DCE,
Automation account, Sentinel onboarding, alert, or role assignment is created.
Destinations and identity use the exact provider v2.7.2 object shapes
(`dataSources.extension[].extensionJson`, not ARM's `extensionSettings`).

Reference profiles were checked against
[Azure's ALZ AMA module](https://github.com/Azure/bicep-registry-modules/blob/main/avm/ptn/alz/ama/main.bicep).
The committed composition is the runtime source of truth; it does not fetch
upstream content during reconciliation.

Outputs: observed `status.id`, `status.immutableId`, and
`status.managedResourceName`. Independent use requires Usages protecting the
workspace, identity and resource group; the management orchestration supplies
these edges. The exact provider schema fixture is under `tests/`.
