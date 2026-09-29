---
icon: lucide/life-buoy
hide:
  - toc
---

# Troubleshooting

Start with the health check. It reports problems across Flux, Crossplane
packages, XRDs, provider pods, and managed-resource activation:

```sh
./infrastructure/verify-crossplane.sh [--context <kube-context>]
```

!!! note "Some bootstrap warnings are temporary"

    Warnings such as `no matches for kind` or `post establish runtime hook
    failed` can remain visible in AKS desktop for about an hour. They are
    harmless if the health check passes.

## A resource does not appear in Azure

Follow the reconciliation chain from Flux to the composite resource (XR),
then to its composed Azure managed resource:

### 1. Check Flux

```sh
flux get kustomizations -A
flux logs --kind Kustomization --name teams --namespace flux-system
```

### 2. Check the composite and recent events

Replace `mvpdagen-app` and `mvpdagen` with the XR name and team namespace:

```sh
kubectl get xresourcegroups.azure.platform.example.org -A
kubectl describe xresourcegroup.azure.platform.example.org/mvpdagen-app \
  -n mvpdagen
kubectl get events -n mvpdagen --sort-by=.lastTimestamp
```

### 3. Check the composed Azure resource

```sh
kubectl get resourcegroups.azure.m.upbound.io -A
kubectl describe resourcegroup.azure.m.upbound.io -n mvpdagen
```

After correcting a team manifest, ask Flux to reconcile it:

```sh
flux reconcile kustomization teams -n flux-system --with-source
```

## A resource kind is not recognized

When a composition uses a new managed-resource kind, its MRD must be listed in
the
[`ManagedResourceActivationPolicy`](https://github.com/sjovang/azure-crossplane-demo/blob/main/clusters/base/crossplane/activation-policies/azure/managed-resource-activation-policy.yaml).
Otherwise Crossplane never creates the CRD for that kind. Add the MRD name in
`<plural>.<group>` form, then wait for Flux to reconcile the activation policy.

## The documentation site is unavailable

The site is public only on AKS. The local kiac cluster has no Gateway,
certificate, or public DNS record.

### The pod reports `ImagePullBackOff`

The GHCR package is private by default, even though the repository is public.
Make the `docs` package public under **Packages > docs > Package settings >
Change visibility**.

### The certificate is not ready

The initial DNS-01 challenge can take two or three minutes to propagate. If
it remains pending, inspect cert-manager:

```sh
kubectl describe certificate -n documentation-site docs-site-tls
kubectl get challenge -A
kubectl logs -n cert-manager deploy/cert-manager
```

A permission error usually means the service principal lost access to
`rg-public-dns`. Let's Encrypt rate-limit errors can happen after repeated
cluster rebuilds; switch the Gateway's
`cert-manager.io/cluster-issuer` annotation to `letsencrypt-staging` while
testing.

### The hostname does not resolve

external-dns reads the address from the Gateway status, not the Service. If
the Gateway is not programmed, no DNS record is written:

```sh
kubectl get kustomization -n flux-system \
  gateway-api-crds external-dns docs-site-gateway
kubectl get configmap -n flux-system azure-dns-config
kubectl get gateway -n documentation-site -o wide
kubectl logs -n external-dns deploy/external-dns
az network dns record-set a list -g rg-public-dns \
  -z demo.liasis.dev -o table
```

If `gateway-api-crds` is blocked by a Helm release Secret larger than 1 MiB,
sync the latest manifests; Flux now applies the rendered CRDs directly. If
`azure-dns-config` is missing, set `docs.acmeEmail` in
[`config-aks.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/infrastructure/config-aks.yaml)
and rerun the AKS bootstrap.

### The site is stale after a push

The docs workflow builds an image and commits its immutable tag back to the
repository. Check that the workflow ran and that the tag in
[`deployment.yaml`](https://github.com/sjovang/azure-crossplane-demo/blob/main/apps/docs/deploy/base/deployment.yaml)
matches the latest commit.

For setup instructions, see [Local cluster setup](kiac.md) or
[AKS cluster setup](aks.md).