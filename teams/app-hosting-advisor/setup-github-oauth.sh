#!/usr/bin/env bash
set +x
set -euo pipefail
unset CLIENT_SECRET

if ! command -v kubectl >/dev/null 2>&1; then
  printf 'Required tool not found: kubectl\n' >&2
  exit 1
fi

kubectl --namespace app-hosting-advisor get namespace app-hosting-advisor >/dev/null

if ! read -rs -p 'OAuth client secret: ' CLIENT_SECRET; then
  printf '\nFailed to read the OAuth client secret.\n' >&2
  exit 1
fi
printf '\n'
if [[ -z "$CLIENT_SECRET" ]]; then
  printf 'OAuth client secret must not be empty.\n' >&2
  exit 1
fi

kubectl --namespace app-hosting-advisor create secret generic \
  appadvisor-github-oauth \
  --from-literal=client-id=Ov23lizwbt9OQknZcGRW \
  --from-file=client-secret=<(printf '%s' "$CLIENT_SECRET") \
  --dry-run=client -o yaml |
  kubectl --namespace app-hosting-advisor apply -f -
unset CLIENT_SECRET
