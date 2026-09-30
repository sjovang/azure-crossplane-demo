#!/usr/bin/env bash
# Creates the kiac cluster, sets up the Azure service principal + secret that
# Crossplane needs, and bootstraps Flux against your fork of this repo.
# Configure via env vars (defaults shown), e.g.:
#   FLUX_BRANCH=my-branch ./infrastructure/bootstrap-kiac.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

credentials_file="$script_dir/azure-credentials-kiac.json"
# Suffix is always appended (even when SP_NAME is overridden) so kiac and aks
# never share the same service principal/clientId across both credential
# files -- see bootstrap-aks.sh for the matching "-aks" suffix.
sp_name="${SP_NAME:-azure-crossplane-demo}-kiac"

# Defaults to your own GitHub user (the fork owner), resolved via the
# authenticated gh CLI. Override if you pushed the fork elsewhere.
FLUX_OWNER="${FLUX_OWNER:-}"
FLUX_REPO="${FLUX_REPO:-azure-crossplane-demo}"
FLUX_BRANCH="${FLUX_BRANCH:-main}"
FLUX_PATH="${FLUX_PATH:-clusters/local}"
# Set to false once the repo is public: ongoing Flux sync then needs no secret.
FLUX_PRIVATE="${FLUX_PRIVATE:-true}"

echo "==> Checking required tools"
require_tools gh kiac flux kubectl az jq

require_github_token
resolve_flux_owner

echo "==> Creating kiac cluster"
kiac create cluster --config "$script_dir/config-kiac.yaml"

echo "==> Ensuring crossplane-system namespace exists"
# Pre-created here (idempotently) so the azure-secret below can be applied
# before Flux/Crossplane are ever bootstrapped. Flux later reconciles the same
# bare Namespace from clusters/base/crossplane/core/namespace.yaml without
# conflict.
ensure_namespace "" crossplane-system

echo "==> Setting up Azure credentials for Crossplane"
ensure_service_principal "$credentials_file" "$sp_name"

echo "==> Granting Microsoft Graph permissions for Entra ID"
ensure_graph_permissions "$credentials_file"

subscription_id=$(jq -r .subscriptionId "$credentials_file")
for provider_namespace in Microsoft.Network Microsoft.Compute; do
  register_azure_provider "$subscription_id" "$provider_namespace"
done

echo "==> Applying azure-secret"
apply_azure_secret "" "$credentials_file"

echo "==> Bootstrapping Flux"
flux_bootstrap_github "" "$FLUX_PATH"
