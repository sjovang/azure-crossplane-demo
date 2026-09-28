# Crossplane conventions

A few things are true of **every** composite resource in this repository.
They are documented once here instead of being repeated on each reference
page.

## Fields Crossplane adds for you

When Crossplane turns a `CompositeResourceDefinition` into a real Kubernetes
API, it injects machinery fields alongside the ones defined in the XRD. You
rarely set these by hand, and they are identical for every composite here.

### `spec.crossplane`

All composites in this repository are `Namespaced`, so these live under
`spec.crossplane` rather than directly on `spec`.

| Field | Purpose |
| --- | --- |
| `compositionRef.name` | Pin to a specific Composition by name |
| `compositionSelector.matchLabels` | Select a Composition by label instead |
| `compositionRevisionRef.name` | Pin to an exact Composition revision |
| `compositionUpdatePolicy` | `Automatic` or `Manual` revision updates |
| `resourceRefs` | Written by Crossplane: the composed resources it created |

Leave all of them unset unless you are deliberately pinning a composition.

### `status.conditions`

Every composite reports standard conditions:

| Condition | Meaning |
| --- | --- |
| `Synced` | Crossplane successfully reconciled the composite |
| `Ready` | Every composed resource reports ready |

```sh
kubectl get composite -n my-team
kubectl describe xvirtualnetwork my-network -n my-team
```

`SYNCED=True` with `READY=False` normally means the manifest is valid and
Azure is still provisioning.

## `status.id`

Every composite in this repository exposes the provisioned Azure resource ID
as `status.id`, populated from the composed managed resource's
`status.atProvider.id`. This is the value you reference when another resource
needs a real Azure identifier.

```sh
kubectl get xnetworkmanageripampool my-pool -n my-team \
  -o jsonpath='{.status.id}'
```

## Referencing other composites

Compositions reference each other by **logical name**, never by Azure resource
ID:

```yaml
spec:
  resourceGroupRef:
    name: my-team        # the metadata.name of an XResourceGroup
```

The referenced composite must live in the same namespace.

## Naming in Azure

Composite names are prefixed when they reach Azure, so the Kubernetes name and
the Azure name are related but not identical:

| Composite | Azure name |
| --- | --- |
| `XResourceGroup` | `rg-<spec.name>` |
| `XNetworkManager` | `vnm-<spec.name>` |
| `XNetworkManagerIPAMPool` | `ipam-<spec.name>` |
| `XVirtualNetwork` | `vnet-<spec.name>` |
| `XVirtualMachine` | `vm-<spec.name>` |

## Immutability

Many fields are immutable, enforced by a CEL rule (`self == oldSelf`). Each
reference page lists them under **Validation rules**. Changing one is
rejected on apply — delete and recreate the composite instead.

## Managed resource activation

Crossplane's default "activate everything" policy is disabled in this
repository, because activating all ~300 Azure managed resource definitions
made the network provider time out and crash.

[`activation-policies/azure/managed-resource-activation-policy.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/activation-policies/azure/managed-resource-activation-policy.yaml)
is the only activation source. If a composition composes a new kind, its MRD
name (`<plural>.<group>`) must be added there or the CRD is never created and
the composite silently never reconciles.
