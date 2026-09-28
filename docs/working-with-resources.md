# Working with Resources

Team resources live in the shared, top-level [`teams/`](../teams/) directory, with one subfolder per team:

- Each team folder has its own [`kustomization.yaml`](../teams/mvpdagen/kustomization.yaml) listing the manifests it wants to deploy.
- The team Kustomization sets the team's namespace and includes the shared [`teams/_base/`](../teams/_base/) Namespace template, so the Namespace is created alongside the team's resources.
- [`teams/kustomization.yaml`](../teams/kustomization.yaml) explicitly registers each team directory for Flux; native Kustomize does not infer team directories or Namespace names automatically.
- The `Crossplane Resources` Grafana dashboard shows composite resources, composed Azure resources, conditions, and composition references.
