#!/usr/bin/env bash
# Reverses infrastructure/bootstrap-kiac.sh: removes the Azure service
# principal created for Crossplane (and its role assignment) and deletes the
# kiac cluster, so nothing is left running/costing money in Azure or locally.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

credentials_file="$script_dir/azure-credentials-kiac.json"

echo "==> Cleaning up Azure service principal"
delete_service_principal "$credentials_file"

echo "==> Deleting kiac cluster"
if command -v kiac >/dev/null 2>&1; then
  cluster_name=$(read_yaml_value "$script_dir/config-kiac.yaml" name)
  if [[ -z "$cluster_name" ]]; then
    echo "Could not read cluster name from $script_dir/config-kiac.yaml." >&2
    exit 1
  fi
  kiac delete cluster --name "$cluster_name"
else
  echo "kiac CLI not found; skipping cluster deletion." >&2
fi
