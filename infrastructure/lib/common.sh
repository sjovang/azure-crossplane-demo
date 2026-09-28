#!/usr/bin/env bash
# Shared helpers for infrastructure/bootstrap-*.sh and teardown-*.sh.
# Sourced (not executed) by the per-target scripts, so this file has no
# shebang execution semantics of its own beyond documentation and does not
# set -euo pipefail here -- the sourcing script owns that.

# read_yaml_value <file> <key>
# Minimal scalar reader for this repo's flat/one-level-nested config YAML
# (top-level keys, or single-indent keys like `addons.<key>`). Strips inline
# comments and surrounding quotes. Not a general YAML parser -- keys must be
# unique within the file (true for config-kiac.yaml/config-aks.yaml today).
read_yaml_value() {
  local file="$1" key="$2"
  awk -v k="$key" '
    $0 ~ "^[[:space:]]*"k":" {
      sub("^[[:space:]]*"k":[[:space:]]*", "")
      sub("[[:space:]]*#.*$", "")
      gsub(/^"|"$/, "")
      print
      exit
    }' "$file"
}

# run_kubectl <context> [kubectl args...]
# <context> may be empty to use kubectl's current context.
run_kubectl() {
  local ctx="$1"
  shift
  if [[ -n "$ctx" ]]; then
    kubectl --context "$ctx" "$@"
  else
    kubectl "$@"
  fi
}

# run_flux <context> [flux args...]
# <context> may be empty to use kubectl's current context.
run_flux() {
  local ctx="$1"
  shift
  if [[ -n "$ctx" ]]; then
    flux --context "$ctx" "$@"
  else
    flux "$@"
  fi
}

# require_tools <tool> [tool...]
# Fails fast with a combined list of every missing CLI.
require_tools() {
  local missing_tools=()
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || missing_tools+=("$tool")
  done
  if (( ${#missing_tools[@]} > 0 )); then
    echo "Missing required tool(s): ${missing_tools[*]}." >&2
    echo "See README.md prerequisites for install instructions." >&2
    for tool in "${missing_tools[@]}"; do
      [[ "$tool" == "kubelogin" ]] && warn_if_wrong_kubelogin
    done
    exit 1
  fi
}

# warn_if_wrong_kubelogin
# Homebrew has two unrelated formulae named "kubelogin": Homebrew core's
# int128/kubelogin (an OIDC plugin providing only kubectl-oidc_login, no
# `kubelogin` binary) and azure/kubelogin's Azure/kubelogin (the one we
# need). Detects the common mix-up and prints a targeted fix.
warn_if_wrong_kubelogin() {
  if command -v kubectl-oidc_login >/dev/null 2>&1 && command -v brew >/dev/null 2>&1 \
    && brew list --formula 2>/dev/null | grep -qx kubelogin; then
    echo >&2
    echo "Detected Homebrew core's 'kubelogin' (int128/kubelogin, OIDC plugin)" >&2
    echo "installed instead of azure/kubelogin/kubelogin. Fix with:" >&2
    echo "  brew uninstall kubelogin" >&2
    echo "  brew tap azure/kubelogin" >&2
    echo "  brew install azure/kubelogin/kubelogin" >&2
  fi
}

# require_github_token
# flux bootstrap always needs API/push access, so GITHUB_TOKEN is required
# regardless of FLUX_PRIVATE.
require_github_token() {
  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "GITHUB_TOKEN is not set. Run 'gh auth login --scopes repo' and" \
         "'export GITHUB_TOKEN=\$(gh auth token)' first." >&2
    exit 1
  fi
}

# resolve_flux_owner
# Defaults FLUX_OWNER to your own GitHub user (the fork owner), resolved via
# the authenticated gh CLI, and fails if it can't be resolved or set.
resolve_flux_owner() {
  FLUX_OWNER="${FLUX_OWNER:-$(gh api user --jq .login 2>/dev/null || true)}"
  if [[ -z "$FLUX_OWNER" ]]; then
    echo "Could not resolve FLUX_OWNER from 'gh api user'. Run 'gh auth login'" \
         "first, or set FLUX_OWNER explicitly." >&2
    exit 1
  fi
}

# ensure_namespace <context> <namespace>
ensure_namespace() {
  local ctx="$1" ns="$2"
  run_kubectl "$ctx" create namespace "$ns" --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -
}

# ensure_service_principal <credentials_file> <sp_name>
# Reuse-if-present semantics: never mints a new service principal if the
# credentials file already exists (re-running bootstrap after a cluster
# recreate must reuse the same SP, not create a fresh one every time).
ensure_service_principal() {
  local credentials_file="$1" sp_name="$2"

  if [[ -f "$credentials_file" ]]; then
    echo "Reusing existing $credentials_file (delete it if you want a fresh service principal)"
    return 0
  fi

  if ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure. Run 'az login' first." >&2
    exit 1
  fi

  local subscription_id tenant_id sp_json client_id client_secret
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
}

# register_azure_provider <subscription_id> <provider_namespace>
# Idempotently registers an Azure resource provider on a subscription.
register_azure_provider() {
  local subscription_id="$1" provider_namespace="$2"
  local registration_state
  registration_state=$(az provider show --namespace "$provider_namespace" \
    --subscription "$subscription_id" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$registration_state" != "Registered" ]]; then
    echo "==> Registering $provider_namespace on subscription $subscription_id"
    az provider register --namespace "$provider_namespace" \
      --subscription "$subscription_id" --wait
  fi
}

# apply_azure_secret <context> <credentials_file>
# Kept at <credentials_file> (gitignored, never deleted) so re-running
# bootstrap after a cluster recreate reuses the same service principal
# instead of minting a new one every time.
apply_azure_secret() {
  local ctx="$1" credentials_file="$2"
  run_kubectl "$ctx" create secret generic azure-secret \
    --namespace crossplane-system \
    --from-file=creds="$credentials_file" \
    --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -
}

# flux_bootstrap_github <context> <path>
# --token-auth: use the GitHub token over HTTPS instead of an SSH deploy key,
# since outbound SSH (port 22) is often blocked on corporate/workshop networks.
flux_bootstrap_github() {
  local ctx="$1" path="$2"
  local ctx_args=()
  [[ -n "$ctx" ]] && ctx_args=(--context="$ctx")
  # ${ctx_args[@]+"${ctx_args[@]}"} (not "${ctx_args[@]}") avoids "unbound
  # variable" under set -u with bash 3.2 (macOS default) when ctx_args is empty.
  flux bootstrap github ${ctx_args[@]+"${ctx_args[@]}"} \
    --owner="$FLUX_OWNER" \
    --repository="$FLUX_REPO" \
    --branch="$FLUX_BRANCH" \
    --path="$path" \
    --private="$FLUX_PRIVATE" \
    --personal \
    --token-auth
}

# delete_service_principal <credentials_file>
# Mirrors ensure_service_principal's reuse semantics: only removes the
# credentials file once the service principal itself is confirmed deleted.
delete_service_principal() {
  local credentials_file="$1"

  if ! command -v az >/dev/null 2>&1; then
    echo "az CLI not found; skipping Azure service principal cleanup." >&2
    echo "If one was created, remove it manually via 'az ad sp delete --id <clientId>'." >&2
    return 0
  fi

  if [[ ! -f "$credentials_file" ]]; then
    echo "No $credentials_file found; skipping Azure service principal cleanup" \
         "(nothing to look up, or it was already cleaned up)."
    return 0
  fi

  local client_id
  client_id=$(jq -r .clientId "$credentials_file")
  if [[ -z "$client_id" || "$client_id" == "null" ]]; then
    echo "Could not read clientId from $credentials_file; skipping Azure cleanup." >&2
    return 0
  fi

  if ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure ('az login'); skipping Azure service principal cleanup." >&2
    echo "The credentials file is kept so you can retry later." >&2
    return 0
  fi

  echo "Deleting service principal $client_id (also removes its role assignment)"
  # az ad sp delete is idempotent: it succeeds even if the SP is already gone.
  if az ad sp delete --id "$client_id"; then
    rm -f "$credentials_file"
    echo "Removed $credentials_file (service principal deleted)."
  else
    echo "Failed to delete service principal $client_id; keeping $credentials_file" \
         "so you can retry or clean it up manually." >&2
  fi
}
