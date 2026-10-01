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

activation_policy_name="azure-resources"
# Only report Warning events young enough to still describe the current
# state. Without a bound, long-resolved events (a FailedScheduling for a pod
# that has since been replaced) print directly above the summary and read as
# live failures even when every check passed.
event_window_minutes=5
# Condition reasons that mean "not actually working" even though the
# condition's status is True. Crossplane reports Healthy=True with reason
# AwaitingActivation for a package whose runtime is scaled to zero because
# none of its ManagedResourceDefinitions are activated: the package is
# installed, but no controller is running and it reconciles nothing.
bad_condition_reasons='["AwaitingActivation"]'
failures=0

pass() { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; failures=$((failures + 1)); }

# check_conditions <resource> <condition...>
# Every object of <resource> must have each listed condition set to True with
# a reason that is not in bad_condition_reasons.
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
  rows=$(jq -r --argjson conds "$conds" --argjson badReasons "$bad_condition_reasons" '
    .items[] | . as $o
    | [$conds[] as $c
       | ($o.status.conditions // [] | map(select(.type == $c)) | first) as $s
       | if ($s.status // "") != "True" then
           "\($c)=\($s.status // "Unknown")\(if $s.message then " (\($s.message))" else "" end)"
         elif ($badReasons | index($s.reason // "")) then
           "\($c)=True but reason=\($s.reason)\(if $s.message then " (\($s.message))" else "" end)"
         else empty
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

echo "==> Package runtimes (crossplane-system)"
# The pod checks below only see pods that exist, so a package whose runtime
# is scaled to zero contributes no row and is silently skipped. Check the
# Deployments directly so an installed-but-not-running package fails loudly.
runtimes=$(run_kubectl "$context" -n crossplane-system get deployments -o json | jq -r '
  .items[]
  | select([.metadata.ownerReferences[]?
            | select(.kind == "ProviderRevision" or .kind == "FunctionRevision")] | length > 0)
  | [.metadata.name, (.spec.replicas // 0 | tostring), (.status.readyReplicas // 0 | tostring)]
  | @tsv')
if [[ -z "$runtimes" ]]; then
  fail "no package runtime Deployments found in crossplane-system"
else
  while IFS=$'\t' read -r deployment desired ready; do
    [[ -z "$deployment" ]] && continue
    if (( desired < 1 )); then
      fail "deployment $deployment: scaled to zero (package installed but no controller running)"
    elif (( ready < desired )); then
      fail "deployment $deployment: $ready/$desired replica(s) ready"
    else
      pass "deployment $deployment ($ready/$desired ready)"
    fi
  done <<<"$runtimes"
fi

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
# The in-cluster policy is authoritative: it is what Crossplane actually acts
# on, and scraping the manifest with awk would pick up any other list item
# added to that file. `|| true` because a missing MRAP must be reported as a
# failure below, not abort the script via set -e/pipefail.
expected=$(run_kubectl "$context" get managedresourceactivationpolicies.apiextensions.crossplane.io \
  "$activation_policy_name" -o json 2>/dev/null | jq -r '.spec.activate[]?' | sort) || true
active=$(run_kubectl "$context" get managedresourcedefinitions.apiextensions.crossplane.io -o json \
  | jq -r '.items[] | select(.spec.state == "Active") | .metadata.name' | sort)
if [[ -z "$expected" ]]; then
  fail "MRAP '$activation_policy_name' not found or has no .spec.activate entries"
else
  unexpected=$(comm -13 <(echo "$expected") <(echo "$active"))
  missing=$(comm -23 <(echo "$expected") <(echo "$active"))
  if [[ -n "$unexpected" ]]; then
    fail "$(grep -c . <<<"$unexpected") active MRD(s) not in $activation_policy_name policy, e.g. $(head -1 <<<"$unexpected")"
  else
    pass "only $activation_policy_name MRDs are active ($(grep -c . <<<"$active" || true))"
  fi
  if [[ -n "$missing" ]]; then
    fail "MRD(s) in policy but not active: $(tr '\n' ' ' <<<"$missing")"
  fi
fi

echo "==> Warning events in crossplane-system, last ${event_window_minutes}m (informational)"
recent_events=$(run_kubectl "$context" -n crossplane-system get events \
  --field-selector type=Warning -o json 2>/dev/null \
  | jq -r --argjson window "$((event_window_minutes * 60))" '
      [.items[]
       | . as $e
       | ($e.lastTimestamp // $e.eventTime // $e.firstTimestamp) as $ts
       | select($ts != null)
       # eventTime carries fractional seconds, which fromdateiso8601 rejects.
       | (try ($ts | sub("\\.[0-9]+"; "") | fromdateiso8601) catch null) as $epoch
       | select($epoch != null and (now - $epoch) <= $window)
       | "  \($ts)  \($e.involvedObject.kind)/\($e.involvedObject.name)  \($e.reason): \($e.message)"]
      | sort | .[-10:][]') || recent_events=""
if [[ -n "$recent_events" ]]; then
  echo "$recent_events"
  echo "  (informational only; these do not affect the result below)"
else
  echo "  none"
fi

echo
if (( failures > 0 )); then
  echo "$failures check(s) failed." >&2
  exit 1
fi
echo "All Crossplane checks passed."
