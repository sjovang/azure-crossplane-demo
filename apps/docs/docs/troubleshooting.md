---
icon: lucide/life-buoy
hide:
  - toc
---

# Troubleshooting

Start with the health check. It reports problems across Flux, Crossplane
packages, XRDs, provider pods, and managed-resource activation:

```sh
./infrastructure/verify-crossplane.sh [--context <kube-context>]
```

!!! note "Some bootstrap warnings are temporary"

    Warnings such as `no matches for kind` or `post establish runtime hook
    failed` can remain visible in AKS desktop for about an hour. They are
    harmless if the health check passes.

## A resource does not appear in Azure

Follow the reconciliation chain from Flux to the composite resource (XR),
then to its composed Azure managed resource:

### 1. Check Flux

```sh
flux get kustomizations -A
flux logs --kind Kustomization --name teams --namespace flux-system
```

### 2. Check the composite and recent events

Replace `mvpdagen-app` and `mvpdagen` with the XR name and team namespace:

```sh
kubectl get xresourcegroups.azure.platform.example.org -A
kubectl describe xresourcegroup.azure.platform.example.org/mvpdagen-app \
  -n mvpdagen
kubectl get events -n mvpdagen --sort-by=.lastTimestamp
```

### 3. Check the composed Azure resource

```sh
kubectl get resourcegroups.azure.m.upbound.io -A
kubectl describe resourcegroup.azure.m.upbound.io -n mvpdagen
```

After correcting a team manifest, ask Flux to reconcile it:

```sh
flux reconcile kustomization teams -n flux-system --with-source
```

## Key Vault secrets already exist but Crossplane tries to create them

The Upbound Key Vault provider tracks a secret with
`crossplane.io/external-name: <secret-name>/<version>`. A bare secret name
does not identify an existing version and causes another create attempt.
An `already exists ... needs to be imported` error with a versioned HTTPS ID
is not evidence of soft deletion. Check active and deleted vault metadata
separately; do not delete or purge either vaults or secrets to clear this error.

The `XKeyVaultSecret` composition preserves the provider-owned version. For
older resources with a bare annotation, it restores the identity from
`status.atProvider.id` only when the vault and secret name match. This does
not read or replace the credential value. Without a matching observed ID,
stop and verify the existing secret's version metadata before adoption.
`keyVaultRef` and `XKeyVault.spec.vaultName` are immutable: replacement needs
new XRs, not an in-place rename or a changed reference.

### Staged vault replacements

The replacement manifests in `teams/app-hosting-advisor/` and `teams/crd-dev/`
are additive. They retain every original XR and its Azure vault/secret,
reuse existing Kubernetes Secret values, and grant the same provider and
App Service identities access through `XKeyVault.spec.appServiceRef`.
Replacement XRs have Flux pruning disabled. Their vault names were available
on 2026-10-29, but availability must be checked again before rollout:

| Existing vault | Replacement vault | Existing value source |
| --- | --- | --- |
| `kv-appadvisor-lsdev` | `kv-advisor-261029-297651` | `appadvisor-github-oauth`: `client-id`, `client-secret` |
| `kv-crd-dev-demo-01` | `kv-crddev-261029-297651` | `keyvault-demo-value`: `password` |
| `kv-webdemo01-5635b6ca` | `kv-web01-261029-297651` | `webdemo01-database-admin`: `password` |

These manifests **prepare replacements, not a consumer cutover**. After
the identity fix and replacement manifests have been reviewed and deployed:

1. Confirm all existing source Secrets and keys still exist. Do not delete
   an ExternalSecret, password generator, or database credential Secret to
   regenerate a missing value. If source continuity is uncertain, stop:
   authorized credential recovery is required before copying or switching.
2. Wait for replacement `XKeyVault` and `XKeyVaultSecret` resources to be
   Ready/Synced with IDs, and both provider/App Service role assignments
   to be Ready at the replacement vault scope. A vault ID alone is not
   proof of working data-plane RBAC. Confirm both OAuth values still match
   the existing OAuth registration and the database password remains valid,
   without logging values or changing the database password.
3. In a separate reviewed cutover, update **both**
   `XAppService/appadvisor.spec.keyVaultSecretEnvVars` vault names in
   `teams/app-hosting-advisor/infrastructure.yaml` to
   `kv-advisor-261029-297651`. Keep their secret names unchanged. The resulting
   App Service URIs must be
   `https://kv-advisor-261029-297651.vault.azure.net/secrets/github-oauth-client-id`
   and
   `https://kv-advisor-261029-297651.vault.azure.net/secrets/github-oauth-client-secret`.
4. `webdemo01` is controlled by `XWebApplication`: its App Service's
   `DATABASE_PASSWORD` setting is generated from its original composed vault
   and secret version. **Do not patch the child App Service or rename the
   original vault.** A follow-up composition change must explicitly select
   the replacement vault and its observed secret version while retaining
   the original composed vault and secret resource keys. The target URI is
   `https://kv-web01-261029-297651.vault.azure.net/secrets/database-password/<replacement-version>`.
   Until that selection exists and is verified, keep the current URI.
   The standalone demo secret has no App Service consumer to cut over.
5. Verify OAuth login, application Key Vault reference resolution, and
   database connectivity. Keep original resources and sources for rollback;
   reverting URI selection must not delete either generation. Retirement
   or purge requires separate authorization and is not part of this rollout.

### A new secret reports `ForbiddenByRbac`

Compare the error's principal object ID with the provider role assignment,
its scope with the exact vault ID, and its role with **Key Vault Secrets
Officer**. The App Service identity separately needs **Key Vault Secrets
User**. Subscription Contributor alone does not grant secret data access.
Initial retries can fail while role assignments are being created or
propagating; do not treat an old warning as a current failure.

On 2026-10-29, `webdemo02` initially failed at 12:04Z, then its secret became
Ready/Synced by 12:04:50Z. Both assignments subsequently became Ready with
the correct principals and scope. No persistent RBAC defect or replacement
vault was needed for that environment. If a 403 persists after assignments
are Ready, investigate current role metadata and authorization errors rather
than rotating credentials, widening roles, or recreating a healthy vault.

## A resource kind is not recognized

When a composition uses a new managed-resource kind, its MRD must be listed in
the
[`ManagedResourceActivationPolicy`](https://github.com/sjovang/azure-crossplane-demo/blob/main/clusters/base/crossplane/activation-policies/azure/managed-resource-activation-policy.yaml).
Otherwise Crossplane never creates the CRD for that kind. Add the MRD name in
`<plural>.<group>` form, then wait for Flux to reconcile the activation policy.

## A provider is installed but does nothing

The health check reports `Healthy=True but reason=AwaitingActivation`, or
`deployment <name>: scaled to zero`:

```sh
kubectl get providers.pkg.crossplane.io \
  -o custom-columns=NAME:.metadata.name,REASON:'.status.conditions[?(@.type=="Healthy")].reason'
```

Crossplane scales a package runtime to zero while none of the MRDs that
package owns are activated, so the provider is installed but reconciles
nothing. Check which MRDs it actually owns before assuming the package is the
right one — provider families split kinds across sub-packages, and
`resourcegroups.azure.m.upbound.io` belongs to `upbound-provider-family-azure`,
not to `provider-azure-resources`:

```sh
kubectl get managedresourcedefinitions.apiextensions.crossplane.io -o json | jq -r \
  '.items[] | select([.metadata.ownerReferences[]?.name] | index("<provider-name>")) |
   "\(.spec.state)\t\(.metadata.name)"'
```

Either add one of those MRDs to the activation policy, or remove the package
if no composition uses its kinds.

## Pods stay Pending with `Insufficient cpu`

`FailedScheduling: 0/N nodes are available: N Insufficient cpu` means total
CPU *requests* exceed allocatable CPU. Compare the two:

```sh
kubectl get nodes -o custom-columns=NAME:.metadata.name,ALLOCATABLE_CPU:.status.allocatable.cpu
kubectl get pods -A -o json | jq -r '[.items[] | select(.status.phase=="Running")
  | [.spec.containers[].resources.requests.cpu // "0"]
  | map(if test("m$") then (.[:-1]|tonumber) else (tonumber*1000) end) | add] | add'
```

Each Crossplane provider and function requests 100m
([`deployment-runtime-config.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/clusters/base/crossplane/providers/azure/deployment-runtime-config.yaml)),
and the `azureMonitorMetrics`, `keda` and `vpa` addons in
[`config-aks.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/infrastructure/config-aks.yaml)
add substantially to `kube-system`. Raise `nodeCount`, pick a larger `vmSize`,
or turn addons off.

A variant mentioning `node(s) had untolerated taint(s)` alongside
`Insufficient cpu` is normal while node auto-provisioning boots a replacement
node: the new node carries a startup taint until it is ready. It resolves on
its own, and the health check only shows Warning events from the last five
minutes so resolved ones do not linger in the output.

## An Entra ID enterprise application is not created

`XEnterpriseApp` needs `Application.ReadWrite.All` on the Crossplane service
principal. `Authorization_RequestDenied` on its Application, Password, or
Principal means the permission is missing or lacks admin consent. Re-run the
bootstrap as an administrator so the existing service principal is upgraded.

## An Entra ID group is not created or updated

`XSecurityGroup` needs Microsoft Graph permissions on the service principal.
`Authorization_RequestDenied` or `failed to validate user` in the XR events
means `Group.ReadWrite.All` or `User.Read.All` is missing or lacks admin
consent. Re-run the bootstrap as an administrator, or ask one to run:

```sh
client_id=$(jq -r .clientId infrastructure/azure-credentials-aks.json)
az ad app permission add --id "$client_id" \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions 62a82d76-70ea-41e2-9197-370581804d09=Role \
  df021288-bdef-4463-88db-98f22de89214=Role
az ad app permission admin-consent --id "$client_id"
```

Use `azure-credentials-kiac.json` for the local cluster. UPNs that do not
exist in the tenant are skipped and listed in `status.unresolvedMembers` or
`status.unresolvedOwners`:

```sh
kubectl get xsecuritygroups.entraid.platform.example.org -A \
  -o custom-columns=NAME:.metadata.name,MEMBERS:.status.unresolvedMembers,OWNERS:.status.unresolvedOwners
```

## An access package or catalog role is not created

`XAccessPackageCatalog` and `XAccessPackage` require
`EntitlementManagement.ReadWrite.All` on the Crossplane service principal.
`Authorization_RequestDenied` means the permission is missing or lacks admin
consent. Re-run the bootstrap as an administrator, or ask one to grant the
permission:

```sh
client_id=$(jq -r .clientId infrastructure/azure-credentials-aks.json)
az ad app permission add --id "$client_id" \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions 9acd699f-1e81-4958-b001-93b1d2506e19=Role
az ad app permission admin-consent --id "$client_id"
```

Use `azure-credentials-kiac.json` for the local cluster. For guest requests,
the referenced `XAccessPackageCatalog` must also set `externallyVisible: true`.

If `GroupResourcesReady=False`, check that every `groupRefs[].name` names a
Ready `XSecurityGroup` in the same namespace with `status.objectId`, and that
the catalog registers every selected package group. Its
`status.groupAssociations` entries must have a nonempty `id` and `ready: true`.
Approver groups configure approvals only; they do not grant package resources.
The `crd-dev-access-demo` group is registered in `crd-dev-access-catalog` and
granted by `crd-dev-access` through name references. After merging, Flux creates
the group and associations automatically; do not copy object IDs or apply
manifests manually.

## An access-package catalog returns `403 UnAuthorized`

The AKS and kiac bootstrap scripts declare and grant
`EntitlementManagement.ReadWrite.All`, but a configured app permission alone
does not prove tenant admin consent. Inspect the Crossplane service principal's
Microsoft Graph application-role assignments and confirm that this role is
assigned to the Microsoft Graph service principal. Re-run the matching
bootstrap as a Global Administrator or Privileged Role Administrator to grant
missing permissions. Catalog creation also requires a Microsoft Entra ID P2
or Microsoft Entra ID Governance license.

## PostgreSQL reports `RegionalAllocationFailed`

`RegionalAllocationFailed` means Azure could not allocate the requested
compute SKU in the selected region; an SKU listed as region-supported is not
a capacity guarantee. Try a supported SKU or region and check current quota
and availability before retrying. Changing region moves the server and its
environment resources and can affect networking and cost.

The `XDatabase.spec.skuName` and `XWebApplication.spec.databaseSkuName`
overrides select the provider-format compute SKU while the existing
`size`/`databaseSize` continues to determine storage. In `crd-dev`, only
`webdemo02` selects `GP_Standard_D2ds_v5`: General Purpose, 2 vCPUs and 8 GiB
RAM, retaining `small` (32 GiB) storage and Sweden Central. Azure's
subscription-scoped `az postgres flexible-server list-skus --location
swedencentral` listed `Standard_D2ds_v5` on 2026-10-29; the provider requires
the `GP_` tier prefix. This moves off the allocation-failing B2s compute
family without adding vCPUs, at a higher compute cost. `webdemo01` and
`dbdemo01` retain `B_Standard_B2s`; shared sizing defaults are unchanged.

Supported SKUs are not a real-time capacity check. Confirm allocation and
the server's Ready/Synced conditions after rollout; database and firewall
reference warnings can persist until the server has an Azure ID. Do not
delete or recreate existing servers to address those downstream warnings.
See Azure's [compute options](https://learn.microsoft.com/azure/postgresql/configure-maintain/concepts-compute)
and [capacity guidance](https://learn.microsoft.com/azure/postgresql/troubleshoot/how-to-resolve-capacity-errors).

## The documentation site is unavailable

The site is public only on AKS. The local kiac cluster has no Gateway,
certificate, or public DNS record.

### The pod reports `ImagePullBackOff`

The GHCR package is private by default, even though the repository is public.
Make the `docs` package public under **Packages > docs > Package settings >
Change visibility**.

### The certificate is not ready

The initial DNS-01 challenge can take two or three minutes to propagate. If
it remains pending, inspect cert-manager:

```sh
kubectl describe certificate -n documentation-site docs-site-tls
kubectl get challenge -A
kubectl logs -n cert-manager deploy/cert-manager
```

A permission error usually means the service principal lost access to
`rg-public-dns`. Let's Encrypt rate-limit errors can happen after repeated
cluster rebuilds; switch the Gateway's
`cert-manager.io/cluster-issuer` annotation to `letsencrypt-staging` while
testing.

### The hostname does not resolve

external-dns reads the address from the Gateway status, not the Service. If
the Gateway is not programmed, no DNS record is written:

```sh
kubectl get kustomization -n flux-system \
  gateway-api-crds external-dns docs-site-gateway
kubectl get configmap -n flux-system azure-dns-config
kubectl get gateway -n documentation-site -o wide
kubectl logs -n external-dns deploy/external-dns
az network dns record-set a list -g rg-public-dns \
  -z demo.liasis.dev -o table
```

If `gateway-api-crds` is blocked by a Helm release Secret larger than 1 MiB,
sync the latest manifests; Flux now applies the rendered CRDs directly. If
`azure-dns-config` is missing, set `docs.acmeEmail` in
[`config-aks.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/infrastructure/config-aks.yaml)
and rerun the AKS bootstrap.

### The site is stale after a push

The docs workflow builds an image and commits its immutable tag back to the
repository. Check that the workflow ran and that the tag in
[`deployment.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/apps/docs/deploy/base/deployment.yaml)
matches the latest commit.

For setup instructions, see [Local cluster setup](kiac.md) or
[AKS cluster setup](aks.md).