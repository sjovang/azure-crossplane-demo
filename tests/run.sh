#!/usr/bin/env bash
# Renders compositions locally with `crossplane render` and either writes a
# golden snapshot of the output or checks the current render still matches
# the stored snapshot. Requires a working Docker daemon (used by
# `crossplane render` to run Composition Functions).
#
# Usage:
#   tests/run.sh snapshot [target ...] # (re)generate golden snapshot(s)
#   tests/run.sh test     [target ...] # render and diff against snapshot(s)
#
# With no targets, every composition directory containing tests/xr.yaml is
# processed. A target may be a composition directory, its directory name, or
# its composite kind (e.g. ./compositions/azure/XAppService).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

usage() {
  echo "usage: $0 <snapshot|test> [composition ...]" >&2
  exit 1
}

resolve_composition() {
  local target="$1"
  local basename_target
  local xrd

  target="${target#./}"
  target="${target%/}"
  if [ -d "$target" ] && [ -f "$target/composition.yaml" ]; then
    echo "$target"
    return
  fi

  basename_target="$(basename "$target")"
  for xrd in compositions/*/*/xrd.yaml; do
    [ -f "$xrd" ] || continue
    if [ "$(basename "$(dirname "$xrd")")" = "$basename_target" ] ||
      grep -qx "    kind: ${basename_target}" "$xrd"; then
      dirname "$xrd"
      return
    fi
  done

  return 1
}

mode="${1:-}"
[ "$mode" = "snapshot" ] || [ "$mode" = "test" ] || usage
shift || true

targets=("$@")
if [ ${#targets[@]} -eq 0 ]; then
  targets=()
  for xr in compositions/*/*/tests/xr.yaml; do
    targets+=("$(dirname "$(dirname "$xr")")")
  done
fi

failures=0
for target in "${targets[@]}"; do
  if ! composition_dir="$(resolve_composition "$target")"; then
    echo "FAIL: composition not found: ${target}" >&2
    failures=$((failures + 1))
    continue
  fi

  name="$(basename "$composition_dir")"
  xr="${composition_dir}/tests/xr.yaml"
  composition="${composition_dir}/composition.yaml"
  snapshot="${composition_dir}/tests/snapshot.yaml"

  if [ ! -f "$xr" ]; then
    echo "FAIL: ${name}: no test case at ${xr}" >&2
    failures=$((failures + 1))
    continue
  fi

  echo "== rendering ${name} =="
  output="$(crossplane render "$xr" "$composition" tests/functions.yaml)"

  if [ "$mode" = "snapshot" ]; then
    echo "$output" >"$snapshot"
    echo "wrote ${snapshot}"
    continue
  fi

  # mode == test
  if [ ! -f "$snapshot" ]; then
    echo "FAIL: ${name}: no snapshot at ${snapshot}, run 'make snapshot' first" >&2
    failures=$((failures + 1))
    continue
  fi
  if ! diff -u "$snapshot" <(echo "$output"); then
    echo "FAIL: ${name}: rendered output no longer matches ${snapshot}" >&2
    failures=$((failures + 1))
  else
    echo "PASS: ${name}"
  fi
done

if [ "$failures" -ne 0 ]; then
  echo "$failures composition test(s) failed" >&2
  exit 1
fi

echo "all composition tests passed"
