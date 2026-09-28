# Troubleshooting

Start with the stack health check. It reports every Flux, Crossplane package,
XRD, pod, and activation problem it finds:

```sh
./infrastructure/verify-crossplane.sh [--context <kube-context>]
```

Warning events from bootstrap (for example `no matches for kind` or
`post establish runtime hook failed`) stay visible in tools like AKS desktop
for about an hour. They're harmless if the script passes.

A composed resource kind needs its managed resource definition (MRD) listed in
[`managed-resource-activation-policy.yaml`](../clusters/base/crossplane/activation-policies/azure/managed-resource-activation-policy.yaml).
Otherwise its CRD is never created.

For a resource that does not appear in Azure, check the deployment chain from Flux to Crossplane:

1. Check Flux reconciliation:

   ```sh
   flux get kustomizations -A
   flux logs --kind Kustomization --name teams --namespace flux-system
   ```

2. Check the team's composite resource and recent events:

   ```sh
   kubectl get xresourcegroups.azure.platform.example.org -A
   kubectl describe xresourcegroup.azure.platform.example.org/mvpdagen-app -n mvpdagen
   kubectl get events -n mvpdagen --sort-by=.lastTimestamp
   ```

3. Check the composed Azure resource:

   ```sh
   kubectl get resourcegroups.azure.m.upbound.io -A
   kubectl describe resourcegroup.azure.m.upbound.io -n mvpdagen
   ```

After changing a team manifest, trigger reconciliation with:

```sh
flux reconcile kustomization teams -n flux-system --with-source
```

## Documentation site

Only on AKS — the local kiac cluster runs the site without a Gateway,
certificate or DNS record.

**The pod is in `ImagePullBackOff`.** The GHCR package is private by default
even though the repository is public. Make it public once under
*Packages → docs → Package settings → Change visibility*.

**The `Certificate` is not `Ready`.** Expect this for the first two or three
minutes: DNS-01 has to write a TXT record into `demo.liasis.dev` and wait for
it to propagate. If it persists:

```sh
kubectl describe certificate -n documentation-site docs-site-tls
kubectl get challenge -A
kubectl logs -n cert-manager deploy/cert-manager
```

A permission error means the service principal lost access to
`rg-public-dns`. Rate-limit errors from Let's Encrypt mean the cluster has
been rebuilt too often — switch the Gateway's
`cert-manager.io/cluster-issuer` annotation to `letsencrypt-staging`, which
issues an untrusted certificate but has far higher limits.

**The hostname does not resolve.** external-dns takes the address from the
Gateway's status, not the Service, so an unprogrammed Gateway means no
record:

```sh
kubectl get gateway -n documentation-site -o wide
kubectl logs -n external-dns deploy/external-dns
az network dns record-set a list -g rg-public-dns -z demo.liasis.dev -o table
```

**The site is stale after a push to `main`.** The image tag is committed back
into the repository by `.github/workflows/docs-site.yaml`. Check that the
workflow ran and that the resulting commit is on `main`; the tag in
`apps/docs/deploy/base/deployment.yaml` should match the latest commit.
