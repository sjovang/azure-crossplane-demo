#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: activate-oci-composition.sh <provider> <name>

Verify the first public OCI release, unsuspend its Flux resources, and make
the teams Kustomization wait for the composition's XRD to be established.
EOF
}

if [[ $# -ne 2 ]]; then
  usage >&2
  exit 1
fi

provider=$1
name=$2

if [[ ! "$provider" =~ ^[a-z0-9]+([a-z0-9-]*[a-z0-9])?$ ]] ||
  [[ ! "$name" =~ ^[a-z0-9]+([a-z0-9-]*[a-z0-9])?$ ]]; then
  echo "Provider and name must use lowercase letters, numbers, and hyphens." >&2
  exit 1
fi

if ! command -v flux >/dev/null 2>&1; then
  echo "flux is required to verify the OCI artifact." >&2
  exit 1
fi

repo_root=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
component="${provider}-${name}"
package_dir="${repo_root}/oci-compositions/${provider}/${name}"
flux_manifest="${repo_root}/clusters/base/crossplane/oci-compositions/${component}.yaml"
teams_kustomization="${repo_root}/clusters/base/teams-kustomization.yaml"
version_file="${package_dir}/.release-please-version"
image="ghcr.io/sjovang/azure-crossplane-demo/compositions/${component}"

for required_file in "$flux_manifest" "$teams_kustomization" "$version_file"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Missing ${required_file#"$repo_root"/}." >&2
    exit 1
  fi
done

version=$(cat "$version_file")
if [[ "$version" == "0.0.0" ]]; then
  echo "The first release has not been created yet." >&2
  exit 1
fi

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-].*)?$ ]]; then
  echo "Invalid release version: $version" >&2
  exit 1
fi

suspend_count=$(grep -c '^  suspend: true$' "$flux_manifest" || true)
if [[ "$suspend_count" -ne 2 ]]; then
  echo "Expected two suspended Flux resources in" >&2
  echo "${flux_manifest#"$repo_root"/}; found ${suspend_count}." >&2
  exit 1
fi

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
mkdir -p "$work_dir/docker" "$work_dir/artifact"
DOCKER_CONFIG="$work_dir/docker" \
  flux pull artifact \
  "oci://${image}:v${version}" \
  --output="$work_dir/artifact"

sed '/^  suspend: true$/d' "$flux_manifest" >"${flux_manifest}.tmp"
mv "${flux_manifest}.tmp" "$flux_manifest"

if ! grep -qx "    - name: composition-${component}" "$teams_kustomization"; then
  printf '    - name: composition-%s\n' "$component" >>"$teams_kustomization"
fi

cat <<EOF
Activated composition-${component}.

Commit these changes so Flux starts reconciling the OCI artifact and makes
teams wait for its XRD to be established.
EOF
