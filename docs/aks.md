# Configuring an AKS cluster + Azure access (for Crossplane)

For a real Azure-hosted cluster instead of (or alongside) kiac, use
[`infrastructure/bootstrap-aks.sh`](../infrastructure/bootstrap-aks.sh) /
[`infrastructure/teardown-aks.sh`](../infrastructure/teardown-aks.sh). It shares
its Azure/Flux logic with the kiac scripts via
[`infrastructure/lib/common.sh`](../infrastructure/lib/common.sh) but creates a
real AKS cluster and resource group instead of a local one.

The cluster is configured to meet the [AKS desktop cluster
requirements](https://github.com/Azure/aks-desktop/blob/main/docs/cluster-requirements.md):
Entra ID authentication + Azure RBAC (hard requirements), and — toggleable in
[`infrastructure/config-aks.yaml`](../infrastructure/config-aks.yaml) — Cilium
network policy, Azure Monitor Metrics, and Managed Grafana (recommended).

> [!NOTE]
> The script fetches an **admin** kubeconfig context (`az aks get-credentials
> --admin`) for its own unattended `kubectl`/`flux bootstrap` calls, bypassing
> interactive Entra ID login. The cluster still satisfies the hard AAD +
> Azure RBAC requirement for aks-desktop and for interactive human users, who
> should instead run `az aks get-credentials` (without `--admin`) plus
> [`kubelogin`](https://azure.github.io/kubelogin/) to authenticate via Entra ID
> — see [Accessing the cluster](#accessing-the-cluster) below.

## Additional prerequisites

- Azure CLI `aks-preview`/`amg` extensions as prompted by `az`, or none if
  the `azureMonitorMetrics`/`managedGrafana` addons are disabled
- [`kubelogin`](https://azure.github.io/kubelogin/): `brew install Azure/kubelogin/kubelogin`

  > [!WARNING]
  > Homebrew has two unrelated formulae named `kubelogin`: plain
  > `brew install kubelogin` (Homebrew core) installs
  > [int128/kubelogin](https://github.com/int128/kubelogin), a generic OIDC
  > plugin that only provides a `kubectl-oidc_login` binary — **not** the
  > `kubelogin` binary this script needs. You must install the
  > `Azure/kubelogin/kubelogin` tap formula specifically. If you already have
  > the wrong one, Homebrew won't let both coexist (same formula name):
  > uninstall it first, then install the correct one:
  > ```sh
  > brew uninstall kubelogin
  > brew tap azure/kubelogin
  > brew install azure/kubelogin/kubelogin
  > ```

## Steps

Follow steps 1, 2, and 6 from the [kiac walkthrough](kiac.md) (fork/clone the
repo, `gh auth login`, `az login`), then:

```sh
export GITHUB_TOKEN=$(gh auth token)
./infrastructure/bootstrap-aks.sh
```

Optional environment variables are the same as `bootstrap-kiac.sh`, except
`FLUX_PATH` defaults to `clusters/aks` and `SP_NAME` defaults to
`azure-crossplane-demo-aks`. Credentials are stored in the gitignored
`infrastructure/azure-credentials-aks.json`.

Tear it down (deletes the service principal and the whole AKS resource
group, including the cluster and Managed Grafana):

```sh
./infrastructure/teardown-aks.sh
```

## Accessing the cluster

`bootstrap-aks.sh` already merges an **admin** context named
`aks-<cluster>-admin` (e.g. `aks-azure-crossplane-demo-admin`, using the
`resourceGroup`/`name` values from
[`infrastructure/config-aks.yaml`](../infrastructure/config-aks.yaml)) into
your local kubeconfig. That context bypasses Entra ID login and exists only
so the bootstrap/teardown scripts can run `kubectl`/`flux` unattended — it's
not meant for regular day-to-day use.

For your own interactive access (respecting the cluster's Entra ID + Azure
RBAC configuration), fetch a **non-admin** context and convert it to use
`kubelogin`:

```sh
az aks get-credentials --resource-group azure-crossplane-demo \
  --name azure-crossplane-demo --context aks-azure-crossplane-demo

# Non-interactive, using your already-authenticated az CLI session:
kubelogin convert-kubeconfig -l azurecli

# Or, if you're not logged in via az CLI, authenticate interactively instead:
kubelogin convert-kubeconfig -l devicecode
```

The first `kubectl` command against this context triggers Entra ID
authentication (silently for `azurecli`, or via a browser/device code for
`devicecode`), and your access is then governed by whatever Azure RBAC role
(e.g. Reader, Writer, Admin) you've been assigned on the cluster.

List and switch between the kiac, AKS admin, and AKS personal contexts as
needed:

```sh
kubectl config get-contexts
kubectl config use-context aks-azure-crossplane-demo
```

> [!TIP]
> If you hold the "Azure Kubernetes Service Cluster Admin Role" and just need
> emergency/admin access without Azure RBAC checks, you can also run
> `az aks get-credentials --admin` yourself interactively — this is the same
> mechanism the bootstrap script uses, just under your own identity.

`teardown-aks.sh` removes the admin context (`aks-<cluster>-admin`) it
created, but not any personal context you fetched yourself — clean those up
with `kubectl config delete-context <name>` if you no longer need them.
