#!/usr/bin/env bash
# Reverses infrastructure/bootstrap-aks.sh: removes the Azure service
# principal created for Crossplane (and its role assignment), deletes the
# whole AKS resource group (cluster + Managed Grafana + everything else in
# it), and cleans up the local kubeconfig context.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

config_file="$script_dir/config-aks.yaml"
credentials_file="$script_dir/azure-credentials-aks.json"

echo "==> Cleaning up Azure service principal"
delete_service_principal "$credentials_file"

echo "==> Removing documentation site DNS records"
# The DNS zone lives in its own resource group, which the group deletion
# below never touches, so external-dns's records have to be removed here.
if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found; skipping DNS record cleanup." >&2
elif [[ ! -f "$config_file" ]]; then
  echo "No $config_file found; skipping DNS record cleanup." >&2
elif ! az account show >/dev/null 2>&1; then
  echo "Not logged in to Azure ('az login'); skipping DNS record cleanup." >&2
else
  dns_zone=$(read_yaml_value "$config_file" dnsZone)
  dns_zone_resource_group=$(read_yaml_value "$config_file" dnsZoneResourceGroup)
  docs_hostname=$(read_yaml_value "$config_file" hostname)
  if [[ -n "$dns_zone" && -n "$dns_zone_resource_group" && -n "$docs_hostname" ]]; then
    delete_dns_records "$dns_zone" "$dns_zone_resource_group" "$docs_hostname"
  else
    echo "No docs DNS configuration in $config_file; nothing to clean up."
  fi
fi

echo "==> Deleting AKS resource group"
if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found; skipping AKS resource group deletion." >&2
elif [[ ! -f "$config_file" ]]; then
  echo "No $config_file found; skipping AKS resource group deletion." >&2
elif ! az account show >/dev/null 2>&1; then
  echo "Not logged in to Azure ('az login'); skipping AKS resource group deletion." >&2
else
  resource_group=$(read_yaml_value "$config_file" resourceGroup)
  if az group show --name "$resource_group" >/dev/null 2>&1; then
    # Deletes the cluster and every other resource in the group (e.g. the
    # Managed Grafana workspace) in one step.
    az group delete --name "$resource_group" --yes
  else
    echo "Resource group $resource_group not found; already deleted."
  fi
fi

echo "==> Removing local kubeconfig context"
if [[ -f "$config_file" ]]; then
  cluster_name=$(read_yaml_value "$config_file" name)
  if [[ -n "$cluster_name" ]]; then
    kube_context="aks-${cluster_name}-admin"
    kubectl config delete-context "$kube_context" >/dev/null 2>&1 || true
    kubectl config delete-cluster "$kube_context" >/dev/null 2>&1 || true
    kubectl config delete-user "clusterAdmin_${cluster_name}" >/dev/null 2>&1 || true
  fi
fi
