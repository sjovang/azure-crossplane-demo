# Subscription activity logs

`XDiagnosticSetting` composes one namespaced
`insights.azure.m.upbound.io/v1beta1 MonitorDiagnosticSetting`.

Inputs: immutable DNS-safe `name`, immutable existing `subscriptionId` UUID,
`workspaceId` (full Log Analytics ARM ID), and optional `providerConfigRef`
(default `ClusterProviderConfig/default`).

The target is `/subscriptions/<uuid>`. Administrative, Security, ServiceHealth,
Alert, Recommendation, Policy, Autoscale and ResourceHealth categories are sent
to the workspace. No DINE policy is needed. This is subscription activity-log
collection, not guest telemetry or workload-resource diagnostic configuration.

Outputs: observed `status.id` and `status.managedResourceName`. No credentials
or workspace keys are exposed. A subscription supports at most five diagnostic
settings; pre-existing settings can exhaust that Azure limit.

Independent use requires a Usage protecting the destination workspace.
Exact provider v2.7.2 schema is recorded under `tests/`.
