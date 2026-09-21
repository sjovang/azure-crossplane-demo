#!/usr/bin/env bash
# Creates or reuses the kiac cluster, sets up the Azure service principal +
# secret that Crossplane needs, optionally creates the Backstage Entra ID app
# registration + secret for OIDC sign-in, and bootstraps Flux against your fork
# of this repo.
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
# Set BACKSTAGE_ENABLED=false to skip building/loading the local Backstage
# image, creating its Entra ID app registration, and applying its Flux
# Kustomization.
BACKSTAGE_ENABLED="${BACKSTAGE_ENABLED:-true}"
KIAC_EXISTING_CLUSTER_ACTION="${KIAC_EXISTING_CLUSTER_ACTION:-}"

# Defaults to your own GitHub user (the fork owner), resolved via the
# authenticated gh CLI. Override if you pushed the fork elsewhere.
FLUX_OWNER="${FLUX_OWNER:-$(gh api user --jq .login 2>/dev/null || true)}"
FLUX_REPO="${FLUX_REPO:-azure-crossplane-demo}"
FLUX_BRANCH="${FLUX_BRANCH:-main}"
# Set to false once the repo is public: ongoing Flux sync then needs no secret.
FLUX_PRIVATE="${FLUX_PRIVATE:-true}"

is_true() {
  case "$1" in
    true|TRUE|True|1|yes|YES|Yes|y|Y|on|ON|On) return 0 ;;
    false|FALSE|False|0|no|NO|No|n|N|off|OFF|Off) return 1 ;;
    *)
      echo "Expected a boolean value, got '$1'." >&2
      return 2
      ;;
  esac
}

check_required_tools() {
  echo "==> Checking required tools"
  case "$BACKSTAGE_ENABLED" in
    true|TRUE|True|1|yes|YES|Yes|y|Y|on|ON|On|false|FALSE|False|0|no|NO|No|n|N|off|OFF|Off) ;;
    *)
      echo "BACKSTAGE_ENABLED must be true or false, got '$BACKSTAGE_ENABLED'." >&2
      exit 1
      ;;
  esac

  missing_tools=()
  required_tools=(gh kiac flux kubectl az jq)
  if is_true "$BACKSTAGE_ENABLED"; then
    required_tools+=(container)
  fi

  for tool in "${required_tools[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || missing_tools+=("$tool")
  done
  if (( ${#missing_tools[@]} > 0 )); then
    echo "Missing required tool(s): ${missing_tools[*]}." >&2
    echo "See README.md prerequisites for install instructions." >&2
    exit 1
  fi
}

check_flux_inputs() {
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
}

check_required_tools
check_flux_inputs

if [[ -z "${FLUX_PATH:-}" ]]; then
  if is_true "$BACKSTAGE_ENABLED"; then
    FLUX_PATH="clusters/dev-with-backstage"
  else
    FLUX_PATH="clusters/dev"
  fi
fi

cluster_name=$(awk '/^name:/{print $2; exit}' "$script_dir/config.yaml")
if [[ -z "$cluster_name" ]]; then
  echo "Could not read cluster name from $script_dir/config.yaml." >&2
  exit 1
fi
cluster_wait=$(awk '/^wait:/{print $2; exit}' "$script_dir/config.yaml")
cluster_wait="${cluster_wait:-5m}"

cluster_exists() {
  kiac get clusters | awk -v name="$cluster_name" 'NR > 1 && $1 == name { found = 1 } END { exit !found }'
}

choose_existing_cluster_action() {
  case "$KIAC_EXISTING_CLUSTER_ACTION" in
    "" ) ;;
    use-existing|use|existing) echo "use-existing"; return 0 ;;
    halt|stop|exit) echo "halt"; return 0 ;;
    redeploy|re-deploy|recreate|delete) echo "redeploy"; return 0 ;;
    *)
      echo "Invalid KIAC_EXISTING_CLUSTER_ACTION='$KIAC_EXISTING_CLUSTER_ACTION'." >&2
      echo "Use one of: use-existing, halt, redeploy." >&2
      return 1
      ;;
  esac

  if [[ ! -t 0 ]]; then
    echo "Cluster '$cluster_name' already exists and no interactive prompt is available." >&2
    echo "Set KIAC_EXISTING_CLUSTER_ACTION to one of: use-existing, halt, redeploy." >&2
    return 1
  fi

  while true; do
    echo "Cluster '$cluster_name' already exists." >&2
    echo "  1) use existing cluster" >&2
    echo "  2) halt" >&2
    echo "  3) re-deploy (delete and recreate '$cluster_name')" >&2
    read -r -p "Choose [1/2/3]: " cluster_action
    case "$cluster_action" in
      1|use-existing|use|existing) echo "use-existing"; return 0 ;;
      2|halt|stop|exit) echo "halt"; return 0 ;;
      3|redeploy|re-deploy|recreate|delete) echo "redeploy"; return 0 ;;
      *) echo "Please choose 1, 2, or 3." >&2 ;;
    esac
  done
}

ensure_cluster() {
  echo "==> Checking kiac cluster '$cluster_name'"
  if cluster_exists; then
    existing_cluster_action=$(choose_existing_cluster_action)
    case "$existing_cluster_action" in
      use-existing)
        echo "==> Using existing kiac cluster '$cluster_name'"
        kiac resume cluster --name "$cluster_name" --wait "$cluster_wait"
        ;;
      halt)
        echo "Leaving existing kiac cluster '$cluster_name' unchanged."
        exit 0
        ;;
      redeploy)
        echo "==> Re-deploying kiac cluster '$cluster_name'"
        kiac delete cluster --name "$cluster_name"
        kiac create cluster --config "$script_dir/config.yaml"
        ;;
    esac
  else
    echo "==> Creating kiac cluster '$cluster_name'"
    kiac create cluster --config "$script_dir/config.yaml"
  fi
}

ensure_container_builder_ready() {
  echo "==> Ensuring container builder is running"
  container builder start

  for _ in {1..30}; do
    if container builder status | awk 'NR > 1 && $1 == "buildkit" && $3 == "running" { found = 1 } END { exit !found }'; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for container builder to become ready." >&2
  echo "Try 'container builder stop && container builder start', then rerun bootstrap." >&2
  return 1
}

ensure_cluster

echo "==> Building and loading the Backstage image"
# kiac clusters have no image registry of their own; `kiac load image` copies
# a locally built image straight onto every node's containerd (the same
# trick `kind load docker-image` uses), so no registry -- in-cluster or
# external -- is ever needed. Re-run this script (or just these two
# commands) after changing clusters/dev/apps/backstage/app/ source, then
# `kubectl rollout restart deployment/backstage -n backstage`.
if is_true "$BACKSTAGE_ENABLED"; then
  ensure_container_builder_ready
  (
    cd "$backstage_app_dir"
    container build -t "$backstage_image" .
  )
  kiac load image "$backstage_image" --name "$cluster_name"
else
  echo "Skipping Backstage image build/load because BACKSTAGE_ENABLED=$BACKSTAGE_ENABLED"
fi

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

if is_true "$BACKSTAGE_ENABLED"; then
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
else
  echo "==> Skipping Backstage setup because BACKSTAGE_ENABLED=$BACKSTAGE_ENABLED"
fi

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
if is_true "$BACKSTAGE_ENABLED"; then
  echo "Once Flux has converged (flux get kustomizations -A), open" \
       "http://$BACKSTAGE_HOSTNAME and sign in with Microsoft Entra ID."
else
  echo "Backstage was disabled. Watch Flux converge with: flux get kustomizations -A"
fi
