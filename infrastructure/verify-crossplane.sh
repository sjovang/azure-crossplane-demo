#!/usr/bin/env bash
# Verifies that the Crossplane stack delivered by Flux is deployed and
# healthy on a cluster: Flux Kustomizations/HelmReleases, Crossplane
# Providers/Functions, XRDs/Compositions, package pods and managed resource
# activation. Exits non-zero if any check fails.
#
# Usage: infrastructure/verify-crossplane.sh [--context <kube-context>]
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$script_dir/lib/common.sh"

context=""
while (( $# > 0 )); do
  case "$1" in
    --context) context="${2:?--context requires a value}"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

require_tools kubectl jq

activation_policy="$script_dir/../clusters/base/crossplane/activation-policies/azure/managed-resource-activation-policy.yaml"
failures=0

pass() { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; failures=$((failures + 1)); }

# check_conditions <resource> <condition...>
# Every object of <resource> must have each listed condition set to True.
check_conditions() {
  local resource="$1"
  shift
  local conds json rows
  conds=$(printf '%s\n' "$@" | jq -R . | jq -sc .)
  json=$(run_kubectl "$context" get "$resource" -A -o json)
  if [[ $(jq '.items | length' <<<"$json") -eq 0 ]]; then
    fail "$resource: none found"
    return
  fi
  rows=$(jq -r --argjson conds "$conds" '
    .items[] | . as $o
    | [$conds[] as $c
       | ($o.status.conditions // [] | map(select(.type == $c)) | first) as $s
       | if ($s.status // "") == "True" then empty
         else "\($c)=\($s.status // "Unknown")\(if $s.message then " (\($s.message))" else "" end)"
         end] as $problems
    | "\(if $o.metadata.namespace then $o.metadata.namespace + "/" else "" end)\($o.metadata.name)\t\($problems | join("; "))"
  ' <<<"$json")
  while IFS=$'\t' read -r name problems; do
    if [[ -z "$problems" ]]; then
      pass "$resource $name"
    else
      fail "$resource $name: $problems"
    fi
  done <<<"$rows"
}

echo "==> Flux"
check_conditions kustomizations.kustomize.toolkit.fluxcd.io Ready
check_conditions helmreleases.helm.toolkit.fluxcd.io Ready

echo "==> Crossplane packages"
check_conditions providers.pkg.crossplane.io Installed Healthy
check_conditions functions.pkg.crossplane.io Installed Healthy

echo "==> Compositions"
check_conditions compositeresourcedefinitions.apiextensions.crossplane.io Established
composition_count=$(run_kubectl "$context" get compositions.apiextensions.crossplane.io -o name | wc -l | tr -d ' ')
if (( composition_count > 0 )); then
  pass "compositions: $composition_count found"
else
  fail "compositions: none found"
fi

echo "==> Package pods (crossplane-system)"
pods=$(run_kubectl "$context" -n crossplane-system get pods -o json | jq -r '
  .items[]
  | select(.metadata.labels["pkg.crossplane.io/revision"] or (.metadata.name | startswith("crossplane")))
  | [.metadata.name, .status.phase,
     ([.status.containerStatuses[]?.ready] | all | tostring),
     ([.status.containerStatuses[]?.restartCount] | add // 0 | tostring)]
  | @tsv')
while IFS=$'\t' read -r pod phase ready restarts; do
  [[ -z "$pod" ]] && continue
  if [[ "$phase" != "Running" || "$ready" != "true" ]]; then
    fail "pod $pod: phase=$phase ready=$ready"
  elif (( restarts > 0 )); then
    fail "pod $pod: $restarts restart(s)"
  else
    pass "pod $pod"
  fi
done <<<"$pods"

echo "==> Managed resource activation"
if run_kubectl "$context" get managedresourceactivationpolicies.apiextensions.crossplane.io default >/dev/null 2>&1; then
  fail "MRAP 'default' exists (chart defaultActivations not disabled; all MRDs activated)"
else
  pass "no chart-default MRAP"
fi
expected=$(awk '/^ *- [a-z0-9.]+$/ {print $2}' "$activation_policy" | sort)
active=$(run_kubectl "$context" get managedresourcedefinitions.apiextensions.crossplane.io -o json \
  | jq -r '.items[] | select(.spec.state == "Active") | .metadata.name' | sort)
unexpected=$(comm -13 <(echo "$expected") <(echo "$active"))
missing=$(comm -23 <(echo "$expected") <(echo "$active"))
if [[ -n "$unexpected" ]]; then
  fail "$(wc -l <<<"$unexpected" | tr -d ' ') active MRD(s) not in azure-resources policy, e.g. $(head -1 <<<"$unexpected")"
else
  pass "only azure-resources MRDs are active ($(wc -l <<<"$active" | tr -d ' '))"
fi
if [[ -n "$missing" ]]; then
  fail "MRD(s) in policy but not active: $(tr '\n' ' ' <<<"$missing")"
fi

echo "==> Recent Warning events in crossplane-system (informational)"
run_kubectl "$context" -n crossplane-system get events --field-selector type=Warning \
  --sort-by=.lastTimestamp 2>/dev/null | tail -n 10 || true

echo
if (( failures > 0 )); then
  echo "$failures check(s) failed." >&2
  exit 1
fi
echo "All Crossplane checks passed."
