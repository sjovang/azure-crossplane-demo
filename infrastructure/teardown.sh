#!/usr/bin/env bash
# Reverses infrastructure/bootstrap.sh: removes the Azure service principal
# created for Crossplane, the Backstage Entra ID app registration (and its
# role assignment / service principal), and deletes the kiac cluster, so
# nothing is left running/costing money in Azure or locally.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
credentials_file="$script_dir/azure-credentials.json"
backstage_credentials_file="$script_dir/backstage-credentials.json"

echo "==> Cleaning up Backstage Entra ID app registration"
if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found; skipping Backstage app registration cleanup." >&2
  echo "If one was created, remove it manually via 'az ad app delete --id <clientId>'." >&2
elif [[ ! -f "$backstage_credentials_file" ]]; then
  echo "No $backstage_credentials_file found; skipping Backstage app registration cleanup" \
       "(nothing to look up, or it was already cleaned up)."
else
  backstage_client_id=$(jq -r .clientId "$backstage_credentials_file")
  if [[ -z "$backstage_client_id" || "$backstage_client_id" == "null" ]]; then
    echo "Could not read clientId from $backstage_credentials_file; skipping cleanup." >&2
  elif ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure ('az login'); skipping Backstage app registration cleanup." >&2
    echo "The credentials file is kept so you can retry later." >&2
  else
    echo "Deleting app registration $backstage_client_id (also removes its service principal)"
    # az ad app delete is idempotent: it succeeds even if the app is already gone.
    if az ad app delete --id "$backstage_client_id"; then
      rm -f "$backstage_credentials_file"
      echo "Removed $backstage_credentials_file (app registration deleted)."
    else
      echo "Failed to delete app registration $backstage_client_id; keeping" \
           "$backstage_credentials_file so you can retry or clean it up manually." >&2
    fi
  fi
fi

echo "==> Cleaning up Azure service principal"
if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found; skipping Azure service principal cleanup." >&2
  echo "If one was created, remove it manually via 'az ad sp delete --id <clientId>'." >&2
elif [[ ! -f "$credentials_file" ]]; then
  echo "No $credentials_file found; skipping Azure service principal cleanup" \
       "(nothing to look up, or it was already cleaned up)."
else
  client_id=$(jq -r .clientId "$credentials_file")
  if [[ -z "$client_id" || "$client_id" == "null" ]]; then
    echo "Could not read clientId from $credentials_file; skipping Azure cleanup." >&2
  elif ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure ('az login'); skipping Azure service principal cleanup." >&2
    echo "The credentials file is kept so you can retry later." >&2
  else
    echo "Deleting service principal $client_id (also removes its role assignment)"
    # az ad sp delete is idempotent: it succeeds even if the SP is already gone.
    if az ad sp delete --id "$client_id"; then
      rm -f "$credentials_file"
      echo "Removed $credentials_file (service principal deleted)."
    else
      echo "Failed to delete service principal $client_id; keeping $credentials_file" \
           "so you can retry or clean it up manually." >&2
    fi
  fi
fi

echo "==> Deleting kiac cluster"
if command -v kiac >/dev/null 2>&1; then
  cluster_name=$(awk '/^name:/{print $2; exit}' "$script_dir/config.yaml")
  if [[ -z "$cluster_name" ]]; then
    echo "Could not read cluster name from $script_dir/config.yaml." >&2
    exit 1
  fi
  kiac delete cluster --name "$cluster_name"
else
  echo "kiac CLI not found; skipping cluster deletion." >&2
fi
