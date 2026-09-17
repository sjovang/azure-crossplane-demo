#!/usr/bin/env bash
# Creates the kiac cluster, sets up the Azure service principal + secret that
# Crossplane needs, and bootstraps Flux against your fork of this repo.
# Configure via env vars (defaults shown), e.g.:
#   FLUX_BRANCH=my-branch ./infrastructure/bootstrap.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
credentials_file="$script_dir/azure-credentials.json"
sp_name="${SP_NAME:-azure-crossplane-demo}"

# Defaults to your own GitHub user (the fork owner), resolved via the
# authenticated gh CLI. Override if you pushed the fork elsewhere.
FLUX_OWNER="${FLUX_OWNER:-$(gh api user --jq .login 2>/dev/null || true)}"
FLUX_REPO="${FLUX_REPO:-azure-crossplane-demo}"
FLUX_BRANCH="${FLUX_BRANCH:-main}"
FLUX_PATH="${FLUX_PATH:-clusters/dev}"
# Set to false once the repo is public: ongoing Flux sync then needs no secret.
FLUX_PRIVATE="${FLUX_PRIVATE:-true}"

echo "==> Checking required tools"
missing_tools=()
for tool in gh kiac flux kubectl az jq; do
  command -v "$tool" >/dev/null 2>&1 || missing_tools+=("$tool")
done
if (( ${#missing_tools[@]} > 0 )); then
  echo "Missing required tool(s): ${missing_tools[*]}." >&2
  echo "See README.md prerequisites for install instructions." >&2
  exit 1
fi

# flux bootstrap always needs API/push access, so GITHUB_TOKEN is required
# regardless of FLUX_PRIVATE.
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is not set. Run 'gh auth login --scopes repo' and" \
       "'export GITHUB_TOKEN=\$(gh auth token)' first." >&2
  exit 1
fi

if [[ -z "$FLUX_OWNER" ]]; then
  echo "Could not resolve FLUX_OWNER from 'gh api user'. Run 'gh auth login'" \
       "first, or set FLUX_OWNER explicitly." >&2
  exit 1
fi

echo "==> Creating kiac cluster"
kiac create cluster --config "$script_dir/config.yaml"

echo "==> Ensuring crossplane-system namespace exists"
# Pre-created here (idempotently) so the azure-secret below can be applied
# before Flux/Crossplane are ever bootstrapped. Flux later reconciles the same
# bare Namespace from clusters/dev/crossplane/core/namespace.yaml without
# conflict.
kubectl create namespace crossplane-system --dry-run=client -o yaml | kubectl apply -f -

echo "==> Setting up Azure credentials for Crossplane"
if [[ -f "$credentials_file" ]]; then
  echo "Reusing existing $credentials_file (delete it if you want a fresh service principal)"
else
  if ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure. Run 'az login' first." >&2
    exit 1
  fi

  subscription_id=$(az account show --query id -o tsv)
  tenant_id=$(az account show --query tenantId -o tsv)

  echo "Creating service principal '$sp_name' (Contributor on subscription $subscription_id)"
  # --sdk-auth is deprecated, so the credentials JSON below is built manually
  # from plain `az` output instead.
  sp_json=$(az ad sp create-for-rbac --name "$sp_name" --role Contributor \
    --scopes "/subscriptions/$subscription_id" -o json)
  client_id=$(echo "$sp_json" | jq -r .appId)
  client_secret=$(echo "$sp_json" | jq -r .password)

  cat > "$credentials_file" <<EOF
{
  "clientId": "$client_id",
  "clientSecret": "$client_secret",
  "subscriptionId": "$subscription_id",
  "tenantId": "$tenant_id",
  "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
  "resourceManagerEndpointUrl": "https://management.azure.com/",
  "activeDirectoryGraphResourceId": "https://graph.windows.net/",
  "sqlManagementEndpointUrl": "https://management.core.windows.net:8443/",
  "galleryEndpointUrl": "https://gallery.azure.com/",
  "managementEndpointUrl": "https://management.core.windows.net/"
}
EOF
  chmod 600 "$credentials_file"
fi

subscription_id=$(jq -r .subscriptionId "$credentials_file")
registration_state=$(az provider show --namespace Microsoft.Network \
  --subscription "$subscription_id" --query registrationState -o tsv)
if [[ "$registration_state" != "Registered" ]]; then
  echo "==> Registering Microsoft.Network on subscription $subscription_id"
  az provider register --namespace Microsoft.Network \
    --subscription "$subscription_id" --wait
fi

echo "==> Applying azure-secret"
# Kept at $credentials_file (gitignored, never deleted) so re-running this
# script after a 'kiac delete cluster' + recreate reuses the same service
# principal instead of minting a new one every time.
kubectl create secret generic azure-secret \
  --namespace crossplane-system \
  --from-file=creds="$credentials_file" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Bootstrapping Flux"
# --token-auth: use the GitHub token over HTTPS instead of an SSH deploy key,
# since outbound SSH (port 22) is often blocked on corporate/workshop networks.
flux bootstrap github \
  --owner="$FLUX_OWNER" \
  --repository="$FLUX_REPO" \
  --branch="$FLUX_BRANCH" \
  --path="$FLUX_PATH" \
  --private="$FLUX_PRIVATE" \
  --personal \
  --token-auth
