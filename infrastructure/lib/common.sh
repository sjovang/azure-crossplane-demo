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

  # Microsoft Graph permissions are granted separately by
  # ensure_graph_permissions so reused service principals get them too.
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

# ensure_role_assignment_permissions <credentials_file>
# Grants Crossplane the least-privilege Azure RBAC administration role needed
# to assign Key Vault access to composed managed identities. Runs on every
# bootstrap so reused service principals are upgraded.
ensure_role_assignment_permissions() {
  local credentials_file="$1"
  local client_id subscription_id scope assignment_id

  client_id=$(jq -r .clientId "$credentials_file")
  subscription_id=$(jq -r .subscriptionId "$credentials_file")
  scope="/subscriptions/$subscription_id"

  assignment_id=$(az role assignment list \
    --assignee "$client_id" \
    --role "Role Based Access Control Administrator" \
    --scope "$scope" \
    --query '[0].id' -o tsv 2>/dev/null) || assignment_id=""

  if [[ -n "$assignment_id" ]]; then
    return 0
  fi

  echo "Granting Role Based Access Control Administrator to service principal $client_id"
  if ! az role assignment create \
    --assignee "$client_id" \
    --role "Role Based Access Control Administrator" \
    --scope "$scope" \
    --output none; then
    echo "WARNING: could not grant Azure role-assignment permissions." >&2
    echo "XWebApplication Key Vault access will not converge until an administrator runs:" >&2
    echo "  az role assignment create --assignee $client_id \\" >&2
    echo "    --role \"Role Based Access Control Administrator\" --scope $scope" >&2
  fi
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
# instead of minting a new one every time. The same JSON is stored twice:
# `creds` for the Crossplane providers and `credentials` for function-msgraph,
# which only reads that key. Non-secret Azure identifiers are also exposed in
# azure-platform-config for Compositions that need them.
apply_azure_secret() {
  local ctx="$1" credentials_file="$2"
  local client_id principal_id
  client_id=$(jq -r .clientId "$credentials_file")
  principal_id=$(az ad sp show --id "$client_id" --query id -o tsv)

  run_kubectl "$ctx" create secret generic azure-secret \
    --namespace crossplane-system \
    --from-file=creds="$credentials_file" \
    --from-file=credentials="$credentials_file" \
    --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -

  run_kubectl "$ctx" create configmap azure-platform-config \
    --namespace crossplane-system \
    --from-literal=tenantId="$(jq -r .tenantId "$credentials_file")" \
    --from-literal=subscriptionId="$(jq -r .subscriptionId "$credentials_file")" \
    --from-literal=principalId="$principal_id" \
    --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -
}

# Microsoft Graph application permissions (app role IDs) needed to manage
# Entra ID applications/groups (provider-azuread) and look up users
# (function-msgraph).
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
GRAPH_APP_ROLES=(
  "Application.ReadWrite.All=1bfefb4e-e0b5-418b-a88f-73c46d2cc8e9"
  "Group.ReadWrite.All=62a82d76-70ea-41e2-9197-370581804d09"
  "User.Read.All=df021288-bdef-4463-88db-98f22de89214"
)

# ensure_graph_permissions <credentials_file>
# Idempotently grants the service principal the GRAPH_APP_ROLES, with admin
# consent. Runs on every bootstrap so reused service principals get upgraded
# too. Consent needs a Global Administrator or Privileged Role Administrator;
# without it this warns and prints the manual commands instead of failing.
ensure_graph_permissions() {
  local credentials_file="$1"
  local client_id sp_id graph_sp_id entry name role_id assigned declared
  local failed=0
  client_id=$(jq -r .clientId "$credentials_file")

  sp_id=$(az ad sp show --id "$client_id" --query id -o tsv 2>/dev/null) || sp_id=""
  graph_sp_id=$(az ad sp show --id "$GRAPH_APP_ID" --query id -o tsv 2>/dev/null) || graph_sp_id=""
  if [[ -z "$sp_id" || -z "$graph_sp_id" ]]; then
    failed=1
  fi

  for entry in "${GRAPH_APP_ROLES[@]}"; do
    [[ "$failed" -eq 0 ]] || break
    name="${entry%%=*}" role_id="${entry#*=}"

    # Declared on the app registration so the portal lists it as configured.
    declared=$(az ad app show --id "$client_id" \
      --query "requiredResourceAccess[?resourceAppId=='$GRAPH_APP_ID'].resourceAccess[] | [?id=='$role_id'] | [0].id" \
      -o tsv 2>/dev/null) || declared=""
    if [[ -z "$declared" ]]; then
      az ad app permission add --id "$client_id" --api "$GRAPH_APP_ID" \
        --api-permissions "$role_id=Role" --only-show-errors || failed=1
    fi

    # The app role assignment is the admin consent.
    assigned=$(az rest --method GET \
      --url "https://graph.microsoft.com/v1.0/servicePrincipals/$sp_id/appRoleAssignments" \
      --query "value[?appRoleId=='$role_id'] | [0].id" -o tsv 2>/dev/null) || assigned=""
    if [[ -z "$assigned" ]]; then
      echo "Granting Microsoft Graph $name to service principal $client_id"
      az rest --method POST \
        --url "https://graph.microsoft.com/v1.0/servicePrincipals/$graph_sp_id/appRoleAssignedTo" \
        --headers "Content-Type=application/json" \
        --body "{\"principalId\":\"$sp_id\",\"resourceId\":\"$graph_sp_id\",\"appRoleId\":\"$role_id\"}" \
        --output none || failed=1
    fi
  done

  if [[ "$failed" -ne 0 ]]; then
    local permissions=""
    for entry in "${GRAPH_APP_ROLES[@]}"; do
      permissions+=" ${entry#*=}=Role"
    done
    echo "WARNING: could not grant Microsoft Graph permissions; Entra ID compositions will fail." >&2
    echo "Ask a Global Administrator or Privileged Role Administrator to run:" >&2
    echo "  az ad app permission add --id $client_id --api $GRAPH_APP_ID --api-permissions$permissions" >&2
    echo "  az ad app permission admin-consent --id $client_id" >&2
  fi
}

# apply_dns_credentials <context> <credentials_file> <dns_zone_resource_group>
# cert-manager and external-dns both authenticate to Azure DNS with the same
# service principal Crossplane uses, in the two different shapes each
# expects. Applied before `flux bootstrap` so neither controller ever starts
# without credentials and sits erroring until a human intervenes.
apply_dns_credentials() {
  local ctx="$1" credentials_file="$2" dns_zone_resource_group="$3"
  local client_id client_secret subscription_id tenant_id

  client_id=$(jq -r .clientId "$credentials_file")
  client_secret=$(jq -r .clientSecret "$credentials_file")
  subscription_id=$(jq -r .subscriptionId "$credentials_file")
  tenant_id=$(jq -r .tenantId "$credentials_file")

  ensure_namespace "$ctx" cert-manager
  ensure_namespace "$ctx" external-dns

  # cert-manager's azureDNS solver takes the client secret on its own; the
  # other values are non-secret and live in the ClusterIssuer manifest.
  run_kubectl "$ctx" create secret generic azuredns-config \
    --namespace cert-manager \
    --from-literal=client-secret="$client_secret" \
    --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -

  # external-dns wants a single azure.json, mounted at /etc/kubernetes.
  local azure_json
  azure_json=$(jq -n \
    --arg tenantId "$tenant_id" \
    --arg subscriptionId "$subscription_id" \
    --arg resourceGroup "$dns_zone_resource_group" \
    --arg aadClientId "$client_id" \
    --arg aadClientSecret "$client_secret" \
    '{
      tenantId: $tenantId,
      subscriptionId: $subscriptionId,
      resourceGroup: $resourceGroup,
      aadClientId: $aadClientId,
      aadClientSecret: $aadClientSecret,
      useManagedIdentityExtension: false
    }')

  run_kubectl "$ctx" create secret generic external-dns-azure-config \
    --namespace external-dns \
    --from-literal=azure.json="$azure_json" \
    --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -
}

# apply_dns_config <context> <credentials_file> <zone> <zone_rg> <hostname>
#                  <acme_email> <cluster_name>
# Non-secret, per-environment values that Flux substitutes into the
# ClusterIssuer, external-dns HelmRelease and Gateway via
# postBuild.substituteFrom. Subscription/tenant/client IDs differ for every
# person who runs this workshop, so they are injected here rather than
# committed to the manifests.
apply_dns_config() {
  local ctx="$1" credentials_file="$2" zone="$3" zone_rg="$4"
  local hostname="$5" acme_email="$6" cluster_name="$7"

  # flux bootstrap creates this namespace itself, but the ConfigMap has to
  # exist before the first reconcile, so create it early and idempotently.
  ensure_namespace "$ctx" flux-system

  run_kubectl "$ctx" create configmap azure-dns-config \
    --namespace flux-system \
    --from-literal=AZURE_CLIENT_ID="$(jq -r .clientId "$credentials_file")" \
    --from-literal=AZURE_SUBSCRIPTION_ID="$(jq -r .subscriptionId "$credentials_file")" \
    --from-literal=AZURE_TENANT_ID="$(jq -r .tenantId "$credentials_file")" \
    --from-literal=DNS_ZONE="$zone" \
    --from-literal=DNS_ZONE_RESOURCE_GROUP="$zone_rg" \
    --from-literal=DOCS_HOSTNAME="$hostname" \
    --from-literal=ACME_EMAIL="$acme_email" \
    --from-literal=CLUSTER_NAME="$cluster_name" \
    --dry-run=client -o yaml \
    | run_kubectl "$ctx" apply -f -
}

# delete_dns_records <zone> <zone_resource_group> <hostname>
# external-dns never gets a chance to clean up when the cluster is deleted
# wholesale, and the DNS zone lives in a resource group that teardown does
# not touch. Removes both the A record and the TXT registry records
# external-dns uses to track ownership, warning rather than failing when
# they are already gone.
delete_dns_records() {
  local zone="$1" zone_resource_group="$2" hostname="$3"
  local record_name="${hostname%".$zone"}"

  if ! az network dns zone show --name "$zone" \
    --resource-group "$zone_resource_group" >/dev/null 2>&1; then
    echo "DNS zone $zone not found in $zone_resource_group, skipping record cleanup"
    return 0
  fi

  # external-dns registers ownership as TXT records alongside the A record,
  # both bare and prefixed with the record type.
  local entry record_type name
  for entry in "a:$record_name" "txt:$record_name" "txt:a-$record_name"; do
    record_type="${entry%%:*}"
    name="${entry#*:}"
    if az network dns record-set "$record_type" show --name "$name" \
      --zone-name "$zone" --resource-group "$zone_resource_group" \
      >/dev/null 2>&1; then
      echo "Deleting $record_type record $name.$zone"
      az network dns record-set "$record_type" delete --name "$name" \
        --zone-name "$zone" --resource-group "$zone_resource_group" \
        --yes >/dev/null
    fi
  done
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
