#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scaffold-oci-composition.sh <provider> <name>

Generate independent release-please and Flux OCI configuration for an
existing package at oci-compositions/<provider>/<name>.
EOF
}

if [[ $# -ne 2 ]]; then
  usage >&2
  exit 1
fi

provider=$1
name=$2

if [[ ! "$provider" =~ ^[a-z0-9]+([a-z0-9-]*[a-z0-9])?$ ]]; then
  echo "Provider must use lowercase letters, numbers, and hyphens." >&2
  exit 1
fi

if [[ ! "$name" =~ ^[a-z0-9]+([a-z0-9-]*[a-z0-9])?$ ]]; then
  echo "Name must use lowercase letters, numbers, and hyphens." >&2
  exit 1
fi

repo_root=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
package_path="oci-compositions/${provider}/${name}"
package_dir="${repo_root}/${package_path}"
component="${provider}-${name}"
workflow="${repo_root}/.github/workflows/release-${component}.yaml"
flux_manifest="${repo_root}/clusters/base/crossplane/oci-compositions/${component}.yaml"
flux_kustomization="${repo_root}/clusters/base/crossplane/oci-compositions/kustomization.yaml"
release_config="${package_dir}/release-please-config.json"
release_manifest="${package_dir}/.release-please-manifest.json"
version_file="${package_dir}/.release-please-version"

for required_file in kustomization.yaml xrd.yaml composition.yaml; do
  if [[ ! -f "${package_dir}/${required_file}" ]]; then
    echo "Missing ${package_path}/${required_file}." >&2
    echo "Create the complete composition package before scaffolding it." >&2
    exit 1
  fi
done

for generated_file in \
  "$workflow" \
  "$flux_manifest" \
  "$release_config" \
  "$release_manifest" \
  "$version_file"; do
  if [[ -e "$generated_file" ]]; then
    echo "Refusing to overwrite ${generated_file#"$repo_root"/}." >&2
    exit 1
  fi
done

replace_template() {
  local source=$1
  local destination=$2
  sed \
    -e "s|__COMPONENT__|${component}|g" \
    -e "s|__PACKAGE_PATH__|${package_path}|g" \
    "$source" >"$destination"
}

replace_template \
  "${repo_root}/scripts/templates/oci-composition-release.yaml" \
  "$workflow"
replace_template \
  "${repo_root}/scripts/templates/oci-composition-flux.yaml" \
  "$flux_manifest"

cat >"$release_config" <<EOF
{
  "\$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "include-component-in-tag": true,
  "packages": {
    "${package_path}": {
      "release-type": "simple",
      "component": "${component}",
      "changelog-path": "CHANGELOG.md",
      "extra-files": [
        ".release-please-version"
      ]
    }
  }
}
EOF

cat >"$release_manifest" <<EOF
{
  "${package_path}": "0.0.0"
}
EOF

printf '0.0.0\n' >"$version_file"

if grep -qx 'resources: \[\]' "$flux_kustomization"; then
  sed \
    "s|^resources: \\[\\]\$|resources:\\
  - ${component}.yaml|" \
    "$flux_kustomization" >"${flux_kustomization}.tmp"
  mv "${flux_kustomization}.tmp" "$flux_kustomization"
elif ! grep -qx "  - ${component}.yaml" "$flux_kustomization"; then
  printf '  - %s.yaml\n' "$component" >>"$flux_kustomization"
fi

cat <<EOF
Created OCI release configuration for ${component}.

Before activating Flux:
1. Merge the release-please PR and verify v1.0.0 was published.
2. Make the GHCR package public.
3. Run ./scripts/activate-oci-composition.sh ${provider} ${name}.
EOF
