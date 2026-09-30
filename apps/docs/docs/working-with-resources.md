---
icon: lucide/boxes
hide:
  - toc
---

# Working with resources

Teams declare Crossplane composites in Git. Flux applies the manifests, and
Crossplane reconciles the Azure resources. Start with a
[team namespace](teams.md), then use this page to add and inspect composites.

## Choose a composition

Browse the [composition reference](reference/index.md) for available APIs,
required fields, defaults, and copyable examples. The
[Crossplane conventions](conventions.md) page explains shared behavior such
as logical-name references, readiness conditions, and immutable fields.

## Add a composite to your team

Create a manifest in your team directory, for example
`teams/my-team/resourcegroup.yaml`:

```yaml
---
apiVersion: azure.platform.example.org/v1alpha1
kind: XResourceGroup
metadata:
  name: application
spec:
  name: application
  location: swedencentral
```

List the manifest in that team's `kustomization.yaml`. The team namespace is
applied by Kustomize, so you do not need to set `metadata.namespace`:

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: my-team
resources:
  - ../_base
  - resourcegroup.yaml
```

Commit and push the change. Flux watches the registered team directory and
applies the manifest; there is no separate imperative create step.

## Reference another composite

Compositions refer to other composites by their Kubernetes `metadata.name`,
not by an Azure resource ID. Keep the referenced composites in the same
namespace:

```yaml
spec:
  resourceGroupRef:
    name: application
```

Here `application` is the `metadata.name` of an `XResourceGroup`. The
composition reference explains which fields each API accepts.

## Check reconciliation

Flux applies the team's Kustomization, then Crossplane reports progress on
each XR:

```sh
flux get kustomizations -A
kubectl get xresourcegroups.azure.platform.example.org -n my-team
kubectl describe xresourcegroup application -n my-team
```

`Synced=True` means the manifest was accepted and reconciled. `Ready=False`
while Azure is provisioning can be normal; inspect the conditions and events
with `kubectl describe` if it stays that way. Once ready, read the provisioned
Azure resource ID from `status.id`:

```sh
kubectl get xresourcegroup application -n my-team \
  -o jsonpath='{.status.id}'
```

## Change or remove a resource

Reference pages mark immutable fields. Changing one is rejected by the API
server; delete and recreate that composite with the desired value instead.

For Git-managed resources, remove the manifest from the team's Kustomization
and commit the change. A direct `kubectl delete` while the manifest remains
in Git may cause Flux to recreate it.

If a resource does not reconcile, follow the checks in
[Troubleshooting](troubleshooting.md).