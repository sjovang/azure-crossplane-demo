#!/usr/bin/env bash
# Creates the kiac cluster, sets up the Azure service principal + secret that
# Crossplane needs, creates the Backstage Entra ID app registration + secret
# for OIDC sign-in, and bootstraps Flux against your fork of this repo.
# Configure via env vars (defaults shown), e.g.:
#   FLUX_BRANCH=my-branch ./infrastructure/bootstrap.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
credentials_file="$script_dir/azure-credentials.json"
backstage_credentials_file="$script_dir/backstage-credentials.json"
# SP_NAME is the common prefix for both Azure identities this script
# creates: the Crossplane service principal (<SP_NAME>-azure-resources) and
# the Backstage Entra ID app registration (<SP_NAME>-backstage). See
# README.md for the least-privilege permissions required to create each.
sp_name="${SP_NAME:-azure-crossplane-demo}"
azure_resources_sp_name="${sp_name}-azure-resources"
backstage_app_name="${sp_name}-backstage"
# Selects how Backstage is reached: "backstage.local" (default) needs a
# manual, sudo-requiring /etc/hosts entry (never written automatically);
# any other value is treated as a domain you control public DNS for, in
# which case you create your own DNS A record instead -- no /etc/hosts
# edit at all. See infrastructure/set-backstage-hostname.sh (called below)
# and clusters/dev/apps/backstage/README.md for both options in detail.
BACKSTAGE_HOSTNAME="${BACKSTAGE_HOSTNAME:-backstage.local}"
backstage_redirect_uri="http://$BACKSTAGE_HOSTNAME/api/auth/microsoft/handler/frame"
backstage_app_dir="$script_dir/../clusters/dev/apps/backstage/app"
backstage_image="backstage:dev"

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
for tool in gh kiac flux kubectl az jq container; do
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

cluster_name=$(awk '/^name:/{print $2; exit}' "$script_dir/config.yaml")
if [[ -z "$cluster_name" ]]; then
  echo "Could not read cluster name from $script_dir/config.yaml." >&2
  exit 1
fi

echo "==> Creating kiac cluster"
kiac create cluster --config "$script_dir/config.yaml"

echo "==> Building and loading the Backstage image"
# kiac clusters have no image registry of their own; `kiac load image` copies
# a locally built image straight onto every node's containerd (the same
# trick `kind load docker-image` uses), so no registry -- in-cluster or
# external -- is ever needed. Re-run this script (or just these two
# commands) after changing clusters/dev/apps/backstage/app/ source, then
# `kubectl rollout restart deployment/backstage -n backstage`.
(
  cd "$backstage_app_dir"
  container build -t "$backstage_image" .
)
kiac load image "$backstage_image" --name "$cluster_name"

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

  echo "Creating service principal '$azure_resources_sp_name' (Contributor on subscription $subscription_id)"
  # --sdk-auth is deprecated, so the credentials JSON below is built manually
  # from plain `az` output instead.
  sp_json=$(az ad sp create-for-rbac --name "$azure_resources_sp_name" --role Contributor \
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
for provider_namespace in Microsoft.Network Microsoft.Compute; do
  registration_state=$(az provider show --namespace "$provider_namespace" \
    --subscription "$subscription_id" --query registrationState -o tsv)
  if [[ "$registration_state" != "Registered" ]]; then
    echo "==> Registering $provider_namespace on subscription $subscription_id"
    az provider register --namespace "$provider_namespace" \
      --subscription "$subscription_id" --wait
  fi
done

echo "==> Applying azure-secret"
# Kept at $credentials_file (gitignored, never deleted) so re-running this
# script after a 'kiac delete cluster' + recreate reuses the same service
# principal instead of minting a new one every time.
kubectl create secret generic azure-secret \
  --namespace crossplane-system \
  --from-file=creds="$credentials_file" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Setting up Backstage Entra ID app registration"
if [[ -f "$backstage_credentials_file" ]]; then
  echo "Reusing existing $backstage_credentials_file (delete it if you want a fresh app registration)"
else
  if ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure. Run 'az login' first." >&2
    exit 1
  fi

  backstage_tenant_id=$(az account show --query tenantId -o tsv)

  echo "Creating app registration '$backstage_app_name'"
  # This app registration is used only for user sign-in (OIDC), never given
  # an Azure RBAC role -- least privilege, unlike the azure-resources SP
  # above which needs Contributor to manage Azure resources.
  backstage_app_json=$(az ad app create \
    --display-name "$backstage_app_name" \
    --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$backstage_redirect_uri" \
    -o json)
  backstage_client_id=$(echo "$backstage_app_json" | jq -r .appId)

  # A service principal (enterprise application) is required for users in
  # this tenant to sign in and for admin consent to take effect.
  az ad sp create --id "$backstage_client_id" >/dev/null

  # Microsoft Graph delegated permissions required by Backstage's Microsoft
  # auth provider (see README.md's permissions table): email, offline_access,
  # openid, profile, User.Read. GUIDs are Microsoft Graph's well-known
  # delegated permission IDs, not secrets.
  graph_api_id="00000003-0000-0000-c000-000000000000"
  for scope_id in \
    64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0 \
    7427e0e9-2fba-42fe-b0c0-848c9e6a8182 \
    37f7f235-527c-4136-accd-4a02d197296e \
    14dad69e-099b-42c9-810b-d002981feec1 \
    e1fe6dd8-ba31-4d61-89e7-88639da4683d; do
    az ad app permission add --id "$backstage_client_id" \
      --api "$graph_api_id" --api-permissions "${scope_id}=Scope" >/dev/null
  done
  # Requires the Application Administrator role (or higher); see README.md.
  az ad app permission admin-consent --id "$backstage_client_id"

  echo "Creating client secret for '$backstage_app_name'"
  backstage_client_secret=$(az ad app credential reset --id "$backstage_client_id" \
    --years 1 --query password -o tsv)

  cat > "$backstage_credentials_file" <<EOF
{
  "clientId": "$backstage_client_id",
  "clientSecret": "$backstage_client_secret",
  "tenantId": "$backstage_tenant_id"
}
EOF
  chmod 600 "$backstage_credentials_file"
fi

echo "==> Ensuring backstage namespace exists"
kubectl create namespace backstage --dry-run=client -o yaml | kubectl apply -f -

echo "==> Applying backstage-entra-secret"
kubectl create secret generic backstage-entra-secret \
  --namespace backstage \
  --from-literal=clientId="$(jq -r .clientId "$backstage_credentials_file")" \
  --from-literal=clientSecret="$(jq -r .clientSecret "$backstage_credentials_file")" \
  --from-literal=tenantId="$(jq -r .tenantId "$backstage_credentials_file")" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Configuring Backstage hostname"
# Idempotent: applies the backstage-vars ConfigMap the apps Flux
# Kustomization's postBuild.substituteFrom reads ${BACKSTAGE_HOSTNAME} from,
# and keeps the Entra app registration's redirect URI in sync -- always
# re-run, even when the app registration/credentials already existed,
# in case BACKSTAGE_HOSTNAME changed since the last run.
BACKSTAGE_HOSTNAME="$BACKSTAGE_HOSTNAME" "$script_dir/set-backstage-hostname.sh"

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

echo "==> Done"
echo "Once Flux has converged (flux get kustomizations -A), open" \
     "http://$BACKSTAGE_HOSTNAME and sign in with Microsoft Entra ID."
