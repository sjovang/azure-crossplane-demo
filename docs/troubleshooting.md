# Troubleshooting

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
