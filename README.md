# Azure Crossplane Demo

Demo environment for managing Azure resources with Crossplane

> [!CAUTION]
> This repository is for demonstrations and workshops only. Do not use it in
> production. It grants broad Azure permissions, stores credentials locally,
> and does not provide the security, reliability, or operational hardening
> required for a production environment.

Flux watches [`clusters/local/`](clusters/local/) (a local [kiac](https://github.com/saiyam1814/kiac)
cluster) and [`clusters/aks/`](clusters/aks/) (a real AKS cluster), both of
which include the shared [`clusters/base/`](clusters/base/) Kustomization.
Other demo clusters can use their own overlay with `../base` while keeping
their generated `flux-system/` manifests and bootstrap path separate.
Crossplane is installed from [`clusters/base/crossplane/`](clusters/base/crossplane/):

```mermaid
flowchart LR
   Bootstrap["bootstrap-kiac.sh /<br/>bootstrap-aks.sh"] --> Credentials["Azure credentials"]
   Credentials --> Secret["azure-secret Secret"]
   Bootstrap --> Flux["Flux"]
   Flux --> Core["core/<br/>Crossplane Helm release"]
   Core --> Provider["providers/azure/<br/>Azure provider"]
   Provider --> Config["provider-configs/azure/<br/>ProviderConfig"]
   Core --> Functions["functions/<br/>Composition functions"]
   Compositions["compositions/<br/>Shared definitions"] --> Functions
```

- [`core/`](clusters/base/crossplane/core/) installs Crossplane through Helm.
- [`providers/azure/`](clusters/base/crossplane/providers/azure/) installs the Azure provider package.
- [`provider-configs/azure/`](clusters/base/crossplane/provider-configs/azure/) configures the provider with Azure credentials.
- [`functions/`](clusters/base/crossplane/functions/) installs the packages used by Compositions.
- [`flux-kustomizations/`](clusters/base/crossplane/flux-kustomizations/) defines the dependency order, so each stage starts only after its required CRDs are ready.
- [`infrastructure/bootstrap-kiac.sh`](infrastructure/bootstrap-kiac.sh) and
  [`infrastructure/bootstrap-aks.sh`](infrastructure/bootstrap-aks.sh) share
  their Azure/Flux logic via [`infrastructure/lib/common.sh`](infrastructure/lib/common.sh)
  and each create the Kubernetes `Secret` from a locally generated service
  principal before bootstrapping Flux.
- Full convergence takes a few minutes after bootstrap. Watch it with `flux get kustomizations -A`, then run `./infrastructure/verify-crossplane.sh`.

> [!NOTE]
> Crossplane `CompositeResourceDefinition`s and `Composition`s live in the
> shared, top-level [`compositions/`](compositions/) directory, outside
> `clusters/base/`. They are grouped first by cloud provider and then by
> composition, allowing multiple clusters to use the same definitions.
> [`compositions/azure/resourcegroup/`](compositions/azure/resourcegroup/) is a
> working example: an `XResourceGroup` in the `azure.platform.example.org` API
> group composing an Azure `ResourceGroup`.

## Documentation

- [Configuring the local kiac cluster](docs/kiac.md) — a local cluster on macOS, fastest to set up.
- [Configuring an AKS cluster](docs/aks.md) — a real Azure-hosted cluster meeting the [aks-desktop cluster requirements](https://github.com/Azure/aks-desktop/blob/main/docs/cluster-requirements.md).
- [Working with Resources](docs/working-with-resources.md)
- [Troubleshooting](docs/troubleshooting.md)
