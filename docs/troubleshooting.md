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
