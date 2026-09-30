---
icon: lucide/laptop
hide:
  - toc
---

# Local cluster setup

[kiac](https://github.com/saiyam1814/kiac) runs a local Kubernetes cluster on
macOS, with each node in its own lightweight VM. This repository configures
three workers plus Prometheus, Grafana, and Traefik; see
[`config-kiac.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/infrastructure/config-kiac.yaml).

## Requirements

- Apple silicon Mac running macOS 26 or later
- Homebrew
- A GitHub account and an Azure subscription where you can create a service
  principal and assign it a role
- Optional: Global Administrator or Privileged Role Administrator in the
  Entra ID tenant, to grant the Microsoft Graph permissions used for Entra ID
  groups

Install the command-line tools:

```sh
brew install gh
brew install fluxcd/tap/flux
brew install azure-cli
brew install jq
brew install kubectl
brew install container
brew install --cask saiyam1814/tap/kiac
```

Start the container system service and check that kiac can use it:

```sh
container system start
kiac doctor
```

## Create the cluster

### 1. Fork the repository

Flux needs to write to your own fork; you will not have push access to the
upstream repository.

```sh
gh auth login
gh repo fork sjovang/azure-crossplane-demo --clone=true
cd azure-crossplane-demo
```

### 2. Sign in to Azure

The bootstrap creates or reuses the service principal used by Crossplane:

```sh
az login
```

### 3. Bootstrap Flux and Crossplane

Run from the cloned repository root:

```sh
export GITHUB_TOKEN="$(gh auth token)"
./infrastructure/bootstrap-kiac.sh
```

The script creates the cluster, stores Azure credentials in the gitignored
`infrastructure/azure-credentials-kiac.json`, registers the Azure resource
providers needed by the examples (Network, Compute, Web, App, Key Vault, and
DBforPostgreSQL), applies the credentials and non-secret platform configuration
to Kubernetes, and then bootstraps Flux. The credentials file is reused on
later runs, so a
rebootstrap does not mint a new service principal. The `-kiac` suffix keeps
this identity separate from the AKS service principal.

!!! note "Service principal permissions"

    The service principal is Contributor and Role Based Access Control
    Administrator on the subscription. The latter lets Crossplane grant
    composed managed identities access to their Key Vault secrets. It also has
    the Microsoft Graph application permissions `Group.ReadWrite.All` and
    `User.Read.All`, used for Entra ID groups. Granting the Graph permissions
    needs a Global Administrator or Privileged Role Administrator. If you are
    neither, the bootstrap prints a warning and the commands to run, and
    continues without them. Only the Entra ID compositions need them.
    Re-running the bootstrap upgrades an existing service principal with any
    missing permissions. If Azure RBAC administration cannot be granted, the
    bootstrap prints the manual command and XWebApplication resources will not
    converge until an administrator runs it.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLUX_OWNER` | GitHub user from `gh` | Repository owner |
| `FLUX_REPO` | `azure-crossplane-demo` | Repository name |
| `FLUX_BRANCH` | `main` | Git branch |
| `FLUX_PATH` | `clusters/local` | Flux overlay |
| `FLUX_PRIVATE` | `true` | Keep the repository private |
| `SP_NAME` | `azure-crossplane-demo` | Base service-principal name; `-kiac` is appended automatically |

## Verify the stack

Allow Flux a few minutes to converge. Then check the Flux and Crossplane
resources:

```sh
./infrastructure/verify-crossplane.sh
flux get kustomizations -A
```

The health check reports Flux readiness, Crossplane packages, XRDs, provider
pods, and managed-resource activation. See [Troubleshooting](troubleshooting.md)
if something is not ready.

## Remove the local cluster

!!! danger "This removes the cluster and service principal"

    Teardown deletes the kiac cluster and its Azure service principal. The
    local credentials file is intentionally retained for reuse if you set up
    the cluster again.

```sh
./infrastructure/teardown-kiac.sh
```

For the Azure-hosted option, see [AKS cluster setup](aks.md).