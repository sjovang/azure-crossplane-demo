#!/usr/bin/env bash
# Configures which hostname Backstage is reached at, without ever touching
# your local machine's /etc/hosts unless you explicitly choose to. Called
# automatically by bootstrap.sh on every run (idempotent), and safe to
# re-run standalone later if you want to switch hostnames without
# re-running the rest of bootstrap.sh, e.g.:
#   BACKSTAGE_HOSTNAME=backstage.example.com ./infrastructure/set-backstage-hostname.sh
#
# Two supported modes, selected purely by the hostname's value:
#   1. backstage.local (default) -- the kiac cluster's Gateway is only
#      reachable via a manual, sudo-requiring /etc/hosts entry; this script
#      never writes it for you, only prints the command.
#   2. any other hostname -- assumed to be a domain you control public DNS
#      for. This script never touches DNS either (it's a third-party
#      provider this repo knows nothing about); it prints the current
#      Traefik LoadBalancer IP so you can create/update an A record
#      yourself.
#
# In both modes this script:
#   - applies the backstage-vars ConfigMap (namespace flux-system) that the
#     apps Flux Kustomization's postBuild.substituteFrom reads
#     ${BACKSTAGE_HOSTNAME} from (see clusters/dev/flux-kustomizations/apps/),
#   - keeps the Backstage Entra ID app registration's redirect URI in sync
#     with the current hostname (az ad app update, idempotent).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backstage_credentials_file="$script_dir/backstage-credentials.json"

backstage_hostname="${1:-${BACKSTAGE_HOSTNAME:-backstage.local}}"

for tool in kubectl; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Required tool '$tool' not found." >&2
    exit 1
  }
done

echo "==> Setting Backstage hostname to $backstage_hostname"

# Pre-created idempotently (like crossplane-system in bootstrap.sh) so this
# can run before 'flux bootstrap' has created flux-system itself.
kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl create configmap backstage-vars \
  --namespace flux-system \
  --from-literal=BACKSTAGE_HOSTNAME="$backstage_hostname" \
  --dry-run=client -o yaml | kubectl apply -f -

if [[ -f "$backstage_credentials_file" ]]; then
  if ! command -v az >/dev/null 2>&1; then
    echo "az CLI not found; skipping Entra redirect URI sync." >&2
    echo "Update it manually: az ad app update --id <clientId> --web-redirect-uris http://$backstage_hostname/api/auth/microsoft/handler/frame" >&2
  elif ! command -v jq >/dev/null 2>&1; then
    echo "jq not found; skipping Entra redirect URI sync." >&2
  elif ! az account show >/dev/null 2>&1; then
    echo "Not logged in to Azure ('az login'); skipping Entra redirect URI sync." >&2
  else
    backstage_client_id=$(jq -r .clientId "$backstage_credentials_file")
    backstage_redirect_uri="http://$backstage_hostname/api/auth/microsoft/handler/frame"
    echo "Syncing redirect URI for app registration $backstage_client_id -> $backstage_redirect_uri"
    az ad app update --id "$backstage_client_id" --web-redirect-uris "$backstage_redirect_uri"
  fi
else
  echo "No $backstage_credentials_file found; skipping Entra redirect URI sync" \
       "(run this after the Backstage app registration has been created)."
fi

echo "==> Next step"
if [[ "$backstage_hostname" == "backstage.local" ]]; then
  echo "Add backstage.local to /etc/hosts (requires sudo, so this is not done" \
       "automatically) once the Traefik Gateway has an IP:"
  echo '  echo "$(kubectl get svc traefik -n kiac-gateway -o jsonpath="{.status.loadBalancer.ingress[0].ip}") backstage.local" | sudo tee -a /etc/hosts'
else
  traefik_ip=$(kubectl get svc traefik -n kiac-gateway \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  if [[ -n "$traefik_ip" ]]; then
    echo "Create (or update) a DNS A record for $backstage_hostname pointing" \
         "at $traefik_ip in your DNS provider. No changes to this machine or" \
         "/etc/hosts are needed. That IP is only reachable from this machine" \
         "(kiac-lb), so the public record won't resolve for anyone else --" \
         "expected for a local workshop cluster."
    echo "The IP can change after a 'kiac delete cluster' + recreate; re-run" \
         "this script (or just re-check 'kubectl get svc traefik -n" \
         "kiac-gateway') and update the DNS record if it does."
  else
    echo "Could not read the Traefik LoadBalancer IP yet. Once it's ready," \
         "create a DNS A record for $backstage_hostname pointing at it:"
    echo "  kubectl get svc traefik -n kiac-gateway"
  fi
fi
