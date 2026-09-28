# Team namespaces

Every team gets its own folder under [`teams/`](https://github.com/sjovang/azure-crossplane-demo/blob/main/teams) and its own
Kubernetes namespace. Adding one is four small steps.

!!! info "Why a namespace per team"

    All compositions in this repository are `Namespaced`, so the composite
    resources, the Secrets they read, and the team's Azure resources all live
    together in one namespace. That keeps teams isolated from each other
    without any extra RBAC wiring.

## 1. Create your folder

Pick a short, lowercase name. It becomes both the folder name and the
namespace name — no prefix, no suffix.

```sh
mkdir -p teams/my-team
```

## 2. Add a `kustomization.yaml`

This is the file that creates your namespace. Setting `namespace:` and
including the shared `../_base` template is what does the work: Kustomize
renames the placeholder Namespace in the template to match, and applies that
namespace to every resource you list.

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: my-team
resources:
  - ../_base
  - resourcegroup.yaml
```

!!! warning "Do not copy `_base` into your folder"

    [`teams/_base/`](https://github.com/sjovang/azure-crossplane-demo/blob/main/teams/_base) is a shared template, not a team.
    Reference it with `../_base`; never list it directly in
    `teams/kustomization.yaml`.

## 3. Add your resources

Create the manifests you referenced above. Every
[reference page](reference/index.md) opens with a working example you can
copy. You do **not** need to set `metadata.namespace` — the Kustomization
applies it for you.

```yaml
---
apiVersion: azure.platform.example.org/v1alpha1
kind: XResourceGroup
metadata:
  name: my-team
spec:
  name: my-team
```

## 4. Register the folder

Finally, add your folder to [`teams/kustomization.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/teams/kustomization.yaml):

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - my-team
```

This step is easy to forget and nothing will fail loudly if you do. Kustomize
and Flux do **not** discover team directories automatically, so an
unregistered folder is simply ignored.

Commit and push. Flux reconciles `teams/` and your namespace and resources
appear.

## Optional: shared defaults for your team

A team Kustomization can patch every composite it owns. `crd-dev` uses this to
pin a region once instead of repeating it in each manifest:

```yaml
patches:
  - target:
      group: azure.platform.example.org
    patch: |-
      - op: add
        path: /spec/location
        value: swedencentral
```

## Checking your work

```sh
# Did Flux apply the team Kustomization?
flux get kustomizations teams

# Did the namespace appear?
kubectl get namespace my-team

# Are your composites reconciling?
kubectl get composite -n my-team
```

If a composite is stuck, the `Common problems` section of its reference page
lists the failures we have actually hit, and
[troubleshooting](https://github.com/sjovang/azure-crossplane-demo/blob/main/docs/troubleshooting.md) covers the rest.
