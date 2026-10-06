# OCI compositions

New compositions in this directory are released independently as Flux OCI
artifacts. They are not included by the Git-backed
[`compositions/kustomization.yaml`](../compositions/kustomization.yaml).

Create the composition package first:

```text
oci-compositions/<provider>/<name>/
├── composition.yaml
├── kustomization.yaml
└── xrd.yaml
```

Every YAML file must start with `---`. The package `kustomization.yaml` must
list the XRD and Composition:

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - xrd.yaml
  - composition.yaml
```

Then generate its release and Flux integration:

```sh
./scripts/scaffold-oci-composition.sh <provider> <name>
```

The command creates:

- separate release-please configuration and semver state in the package;
- a dedicated `.github/workflows/release-<provider>-<name>.yaml` workflow;
- a Flux `OCIRepository` and `Kustomization` registered under
  `clusters/base/crossplane/oci-compositions/`.

The workflow publishes releases to:

```text
ghcr.io/sjovang/azure-crossplane-demo/compositions/<provider>-<name>:vX.Y.Z
```

Release-please reads Conventional Commit messages that affect only that
package. Merge its release PR to create the GitHub release and publish the
matching semver OCI artifact.

## Activate the Flux source

Generated Flux resources start with `suspend: true`. This prevents a new
package from making the cluster unhealthy before its first artifact exists.

After the first release:

1. Make the GHCR package public. GHCR creates packages as private even when
   this repository is public.
2. Run:

   ```sh
   ./scripts/activate-oci-composition.sh <provider> <name>
   ```

   The command verifies the exact released artifact, removes both suspend
   fields, and makes the teams Kustomization depend on the OCI composition.
3. Commit the changes. Flux then selects the latest compatible `v1.x`
   release using the source's semver constraint.

The generated source intentionally follows only the current major release.
Update its `spec.ref.semver` constraint explicitly when adopting a breaking
major version.

Keep the source suspended if the package must remain private; private
registries require an explicit Flux pull secret that is not managed here.
