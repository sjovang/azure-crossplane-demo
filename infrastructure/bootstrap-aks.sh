#!/usr/bin/env bash
# Creates a real AKS cluster meeting the aks-desktop cluster requirements
# (https://github.com/Azure/aks-desktop/blob/main/docs/cluster-requirements.md),
# sets up the Azure service principal + secret that Crossplane needs, and
# bootstraps Flux against your fork of this repo.
# Configure via env vars (defaults shown), e.g.:
#   FLUX_BRANCH=my-branch ./infrastructure/bootstrap-aks.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

config_file="$script_dir/config-aks.yaml"
credentials_file="$script_dir/azure-credentials-aks.json"
# Suffix is always appended (even when SP_NAME is overridden) so aks and kiac
# never share the same service principal/clientId across both credential
# files -- see bootstrap-kiac.sh for the matching "-kiac" suffix.
sp_name="${SP_NAME:-azure-crossplane-demo}-aks"

# Defaults to your own GitHub user (the fork owner), resolved via the
# authenticated gh CLI. Override if you pushed the fork elsewhere.
FLUX_OWNER="${FLUX_OWNER:-}"
FLUX_REPO="${FLUX_REPO:-azure-crossplane-demo}"
FLUX_BRANCH="${FLUX_BRANCH:-main}"
FLUX_PATH="${FLUX_PATH:-clusters/aks}"
# Set to false once the repo is public: ongoing Flux sync then needs no secret.
FLUX_PRIVATE="${FLUX_PRIVATE:-true}"

echo "==> Checking required tools"
# kubelogin is required because --enable-aad --enable-azure-rbac clusters
# need it to convert kubeconfigs for Entra ID authentication.
require_tools gh flux kubectl az jq kubelogin

require_github_token
resolve_flux_owner

resource_group=$(read_yaml_value "$config_file" resourceGroup)
cluster_name=$(read_yaml_value "$config_file" name)
location=$(read_yaml_value "$config_file" location)
k8s_version=$(read_yaml_value "$config_file" k8sVersion)
node_count=$(read_yaml_value "$config_file" nodeCount)
vm_size=$(read_yaml_value "$config_file" vmSize)
addon_network_policy=$(read_yaml_value "$config_file" networkPolicy)
addon_azure_monitor_metrics=$(read_yaml_value "$config_file" azureMonitorMetrics)
addon_managed_grafana=$(read_yaml_value "$config_file" managedGrafana)
addon_keda=$(read_yaml_value "$config_file" keda)
addon_vpa=$(read_yaml_value "$config_file" vpa)

# kubectl/flux talk to a dedicated admin context for this cluster so this
# script never depends on (or clobbers) whatever context is currently active,
# e.g. from a kiac bootstrap run in the same shell. `az aks get-credentials
# --admin` always appends its own "-admin" suffix to whatever --context name
# is given (it takes precedence over --context), so the base name passed to
# get-credentials must NOT already end in "-admin" or it becomes "-admin-admin".
kube_context_base="aks-${cluster_name}"
kube_context="${kube_context_base}-admin"

if ! az account show >/dev/null 2>&1; then
  echo "Not logged in to Azure. Run 'az login' first." >&2
  exit 1
fi
subscription_id=$(az account show --query id -o tsv)

echo "==> Registering Azure resource providers"
# Microsoft.ContainerService must be registered before az aks create.
for provider_namespace in Microsoft.ContainerService Microsoft.Network Microsoft.Compute; do
  register_azure_provider "$subscription_id" "$provider_namespace"
done

echo "==> Ensuring resource group $resource_group exists"
az group create --name "$resource_group" --location "$location" --output none

echo "==> Creating AKS cluster $cluster_name"
if az aks show --resource-group "$resource_group" --name "$cluster_name" >/dev/null 2>&1; then
  echo "AKS cluster $cluster_name already exists in $resource_group; skipping creation."
else
  create_args=(
    --resource-group "$resource_group"
    --name "$cluster_name"
    --location "$location"
    --node-count "$node_count"
    --node-vm-size "$vm_size"
    --generate-ssh-keys
    # Hard requirements from cluster-requirements.md: Entra ID authentication
    # + Azure RBAC for Kubernetes authorization.
    --enable-aad
    --enable-azure-rbac
  )
  [[ -n "$k8s_version" ]] && create_args+=(--kubernetes-version "$k8s_version")
  if [[ "$addon_network_policy" == "true" ]]; then
    # Recommended: a network policy engine. Immutable after creation, so it
    # must be set here. --network-policy cilium requires --network-dataplane
    # cilium to be set explicitly (it does not default to cilium just
    # because --network-policy is cilium).
    create_args+=(--network-plugin azure --network-dataplane cilium --network-policy cilium)
  fi
  if [[ "$addon_azure_monitor_metrics" == "true" ]]; then
    # Recommended: Azure Monitor Metrics (Managed Prometheus).
    create_args+=(--enable-azure-monitor-metrics)
  fi
  if [[ "$addon_keda" == "true" ]]; then
    # Recommended: KEDA event-driven autoscaling.
    create_args+=(--enable-keda)
  fi
  if [[ "$addon_vpa" == "true" ]]; then
    # Recommended: Vertical Pod Autoscaler.
    create_args+=(--enable-vpa)
  fi
  az aks create "${create_args[@]}"
fi

if [[ "$addon_managed_grafana" == "true" ]]; then
  echo "==> Ensuring Managed Grafana workspace"
  # Managed Grafana workspace names must be 2-23 characters, so truncate
  # the cluster name to leave room for the "-graf" suffix rather than
  # hardcoding a name unrelated to config-aks.yaml.
  grafana_name="${cluster_name:0:18}-graf"
  if ! az grafana show --resource-group "$resource_group" --name "$grafana_name" >/dev/null 2>&1; then
    az grafana create --resource-group "$resource_group" --name "$grafana_name" --location "$location"
  fi
  grafana_id=$(az grafana show --resource-group "$resource_group" --name "$grafana_name" --query id -o tsv)
  echo "==> Linking Managed Grafana to AKS metrics"
  az aks update --resource-group "$resource_group" --name "$cluster_name" \
    --enable-azure-monitor-metrics --grafana-resource-id "$grafana_id"
fi

echo "==> Fetching AKS credentials (admin, dedicated context)"
# --admin bypasses interactive Entra ID login for this script's own
# unattended kubectl/flux calls; the cluster still satisfies the hard
# AAD+Azure RBAC requirement for aks-desktop and interactive human users
# (who authenticate via 'az aks get-credentials' without --admin + kubelogin).
az aks get-credentials --resource-group "$resource_group" --name "$cluster_name" \
  --admin --overwrite-existing --context "$kube_context_base"

echo "==> Ensuring crossplane-system namespace exists"
ensure_namespace "$kube_context" crossplane-system

echo "==> Setting up Azure credentials for Crossplane"
ensure_service_principal "$credentials_file" "$sp_name"

echo "==> Applying azure-secret"
apply_azure_secret "$kube_context" "$credentials_file"

echo "==> Bootstrapping Flux"
flux_bootstrap_github "$kube_context" "$FLUX_PATH"
