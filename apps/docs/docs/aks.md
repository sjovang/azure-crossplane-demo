---
icon: lucide/cloud-cog
hide:
  - toc
---

# AKS cluster setup

Use the AKS bootstrap to run this platform on Azure Kubernetes Service. It
creates the cluster, configures Flux and Crossplane, and publishes this docs
site through Azure DNS and Gateway API.

!!! important "Automation and personal access use different contexts"

    The bootstrap fetches an admin kubeconfig for its unattended Flux setup.
    For day-to-day work, use a non-admin context authenticated through Entra ID
    and `kubelogin`.

## Before you start

Install and authenticate these tools:

- GitHub CLI (`gh`), Flux CLI (`flux`), `kubectl`, Azure CLI (`az`), and `jq`
- [`kubelogin`](https://azure.github.io/kubelogin/)
- An Azure subscription where you can create a resource group and service
  principal
- Optional: Global Administrator or Privileged Role Administrator in the
  Entra ID tenant, to grant the Microsoft Graph permissions used for Entra ID
  groups

The bootstrap requires the Azure kubelogin binary. Install the Azure tap
formula specifically:

```sh
brew tap azure/kubelogin
brew install azure/kubelogin/kubelogin
```

!!! warning "Use the Azure kubelogin package"

    `brew install kubelogin` installs a different OIDC plugin that provides
    `kubectl-oidc_login`, not the `kubelogin` binary required here. If it is
    already installed, remove it before installing the Azure formula:

    ```sh
    brew uninstall kubelogin
    brew tap azure/kubelogin
    brew install azure/kubelogin/kubelogin
    ```

Authenticate the CLIs and select the Azure subscription you want to use:

```sh
gh auth login
az login
```

## Configure the docs hostname

The DNS zone must already exist in Azure. Set its details in
[`config-aks.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/infrastructure/config-aks.yaml):

```yaml
docs:
  dnsZone: demo.liasis.dev
  dnsZoneResourceGroup: rg-public-dns
  hostname: docs.demo.liasis.dev
  acmeEmail: you@example.com
```

The bootstrap manages the `docs` record, not the DNS zone itself. It refuses
to continue if the zone is missing or `acmeEmail` is empty.

## Bootstrap

Run this from the repository root:

```sh
export GITHUB_TOKEN="$(gh auth token)"
./infrastructure/bootstrap-aks.sh
```

The script creates the `azure-crossplane-demo` resource group and AKS cluster,
registers the Azure resource providers needed by the cluster and examples
(ContainerService, Network, Compute, Web, App, and DBforPostgreSQL), and
bootstraps Flux against
your fork. It stores Crossplane credentials in the gitignored
`infrastructure/azure-credentials-aks.json` and reuses that file on later
runs. The service principal always receives an `-aks` suffix, separate from
the local kiac identity.

`FLUX_PATH` defaults to `clusters/aks`. The script also fetches an admin
kubeconfig context such as `aks-azure-crossplane-demo-admin` so its own
`kubectl` and Flux operations do not require an interactive Entra ID login.
Other optional environment variables match the
[local cluster bootstrap](kiac.md); `SP_NAME` always receives an `-aks`
suffix (`azure-crossplane-demo-aks` by default).

Azure CLI may prompt to install extensions when Azure Monitor Metrics or
Managed Grafana are enabled in the config. No extension is needed when both
addons are disabled.

## Verify the cluster

Allow Flux a few minutes to converge, then check Crossplane and the docs
endpoint:

```sh
./infrastructure/verify-crossplane.sh --context aks-azure-crossplane-demo-admin
kubectl get certificate -n documentation-site
kubectl get gateway -n documentation-site
curl -sI https://docs.demo.liasis.dev | head -1
```

The first certificate can take several minutes while the DNS-01 challenge
propagates. If you are rebuilding repeatedly, switch the Gateway issuer to
`letsencrypt-staging` to avoid production rate limits.

## What runs on AKS

AKS enables Entra ID authentication and Azure RBAC. Optional settings in
[`config-aks.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/infrastructure/config-aks.yaml)
include Cilium network policy, Azure Monitor Metrics, Managed Grafana, KEDA,
and VPA. The cluster also installs these components to serve the docs site:

| Component | Role |
| --- | --- |
| Gateway API CRDs | Rendered from the Envoy Gateway chart and applied directly by Flux; the bundle exceeds Helm's 1 MiB release limit. |
| Envoy Gateway | Terminates TLS and routes traffic. It replaces the retired `ingress-nginx` controller. |
| cert-manager | Issues the site's Let's Encrypt certificate through DNS-01. |
| external-dns | Writes the `docs.demo.liasis.dev` record using the Gateway address. |

??? info "Regenerate the Gateway API CRD bundle"

    The checked-in CRDs come from Envoy Gateway chart version 1.9.2, using its
    standard Gateway API channel. When upgrading the pinned chart, render them
    directly rather than through a Helm release:

    ```sh
    helm template gateway-api-crds \
      oci://docker.io/envoyproxy/gateway-crds-helm \
      --version 1.9.2 --namespace envoy-gateway-system \
      --set crds.gatewayAPI.enabled=true \
      --set crds.gatewayAPI.channel=standard \
      --set crds.envoyGateway.enabled=true
    ```

The bootstrap creates the secrets and ConfigMaps used by these components,
including the non-secret `azure-platform-config` consumed by environment
Compositions. Subscription-specific values are not committed to the
repository.

!!! note "Service principal permissions"

    The service principal is Contributor and Role Based Access Control
    Administrator on the subscription. The latter lets Crossplane grant
    composed managed identities access to their Key Vault secrets. It also has
    the Microsoft Graph application permissions `Application.ReadWrite.All`,
    `Group.ReadWrite.All`, and `User.Read.All`, used for Entra ID applications,
    groups, and user lookup. Granting the Graph permissions needs a Global
    Administrator or Privileged Role Administrator. If you are neither, the
    bootstrap prints a warning and the commands to run, and continues without
    them. Only the Entra ID compositions need them.
    Re-running the bootstrap upgrades an existing service principal with any
    missing permissions. If Azure RBAC administration cannot be granted, the
    bootstrap prints the manual command and XWebApplication resources will not
    converge until an administrator runs it.

### Node capacity

The system `nodepool1` uses the configured node count and allows 50 pods per
node, set by `maxPods` in `infrastructure/config-aks.yaml` when the cluster is
created. AKS Node Auto-Provisioning (NAP) adds a separate `workshop` pool of
Linux D-series nodes on demand; its pod limit is configured independently in
the `AKSNodeClass`. It can scale back to zero and has an aggregate limit of 16
vCPU and 64 GiB; Azure quota and VM availability may lower the practical
limit. Workloads are not restricted to a single pool.

The bootstrap disables AKS's uncapped default NAP pools. Flux applies the
custom pool only after NAP installs its CRDs. Inspect provisioning with:

```sh
az aks show -g azure-crossplane-demo -n azure-crossplane-demo \
  --query nodeProvisioningProfile
kubectl get nodepool,aksnodeclass,nodeclaim
kubectl get nodes -L karpenter.sh/nodepool
```

The custom pool is defined in
[`node-pool.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/clusters/aks/nap-workloads/node-pool.yaml).

## Access the cluster

The admin context created by bootstrap is for automation. For personal access,
fetch a non-admin context and configure `kubelogin`:

```sh
az aks get-credentials --resource-group azure-crossplane-demo \
  --name azure-crossplane-demo --context aks-azure-crossplane-demo

# Use your existing Azure CLI session:
kubelogin convert-kubeconfig -l azurecli

# Or authenticate interactively with a device code:
kubelogin convert-kubeconfig -l devicecode

kubectl config use-context aks-azure-crossplane-demo
```

Your access is governed by the Azure RBAC role assigned to you. Use this
non-admin context with
[*AKS desktop*](https://aka.ms/aks/aks-desktop), which requires Entra ID and
Azure RBAC. The bootstrap removes its admin context during teardown, but does
not remove personal contexts you fetched yourself.

## Tear down

!!! danger "This deletes the AKS resource group"

    Teardown deletes the AKS cluster, Managed Grafana, the AKS service
    principal, and the DNS record created for the docs site. It does not delete
    the DNS zone or its resource group.

```sh
./infrastructure/teardown-aks.sh
```

For the local alternative, see [Local cluster setup](kiac.md). For issues
after bootstrap, see [Troubleshooting](troubleshooting.md).