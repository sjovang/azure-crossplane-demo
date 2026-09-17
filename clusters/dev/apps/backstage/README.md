# Developer Portal (Backstage)

> [!NOTE]
> This is a base configuration only: Microsoft Entra ID (Azure AD) OIDC
> sign-in, no catalog integration with Crossplane resources yet. Backstage
> is exposed at `http://backstage.local` through the kiac cluster's Gateway
> API addon (Traefik), not `kubectl port-forward`.

Architecture:

- This directory holds everything Backstage, split into the two
  team-folder-style subfolders used elsewhere in this repo:
  - [`app/`](app/) — the Backstage application source (a standard
    `create-app`-style monorepo: `packages/backend`, `packages/app`), its
    `app-config.yaml`/`app-config.production.yaml`, and its `Dockerfile`.
    This is the only application source code in the repo — everything else
    here is declarative manifests.
  - [`infra/`](infra/) — the Kubernetes `Namespace`, `Deployment`,
    `Service`, and `HTTPRoute` for Backstage, applied by the dependent
    `apps` Flux Kustomization (`clusters/dev/apps-kustomization.yaml`,
    `dependsOn: flux-system`, `path: ./clusters/dev/apps/backstage/infra`).
    Only `infra/` is ever applied by Flux — `app/` is not Kubernetes YAML.
- [`infra/httproute.yaml`](infra/httproute.yaml) attaches Backstage to the
  `kiac` `Gateway` (namespace `kiac-gateway`, `GatewayClass traefik`) that
  [kiac's gateway addon](https://github.com/saiyam1814/kiac/blob/main/examples/gateway-api-lab.md)
  pre-creates on cluster creation
  (`infrastructure/config.yaml`'s `addons.gateway: true`), at the hostname
  `backstage.local`. No Ingress/`type: LoadBalancer` Service is created
  directly for Backstage; Traefik's own `LoadBalancer` Service in
  `kiac-gateway` is the single entry point shared by every `HTTPRoute` on
  the cluster.

## Prerequisites

- Node.js 20 or 22
- A container registry you can push to (e.g. GHCR, Docker Hub, ACR)
- A kiac cluster created with the gateway addon enabled
  (`infrastructure/config.yaml`'s `addons.gateway: true`, already the repo
  default)
- Run [`infrastructure/bootstrap.sh`](../../../../infrastructure/bootstrap.sh)
  first (see the top-level [README.md](../../../../README.md)) — it already
  creates the `<SP_NAME>-backstage` Entra ID app registration (redirect URI
  `http://backstage.local/api/auth/microsoft/handler/frame`, the
  least-privilege Microsoft Graph permissions Backstage needs, and admin
  consent) and applies its credentials as the `backstage-entra-secret`
  Kubernetes `Secret`. The top-level README's prerequisites table lists the
  exact Azure/Entra ID roles required to run it.

## Steps

1. **Build and push the Backstage image** (from this directory's
   [`app/`](app/) subfolder):

   ```sh
   cd clusters/dev/apps/backstage/app
   npm install
   docker build -t <your-registry>/backstage:latest .
   docker push <your-registry>/backstage:latest
   ```

   Update the `image:` field in
   [`infra/deployment.yaml`](infra/deployment.yaml) to match the image you
   pushed.

2. **Reconcile Flux and verify**:

   ```sh
   flux reconcile kustomization apps -n flux-system --with-source
   flux get kustomizations -A
   kubectl get httproute backstage -n backstage -o wide
   ```

   A healthy `HTTPRoute` shows an attached `Gateway` and
   `Accepted=True`/`ResolvedRefs=True` conditions in
   `kubectl describe httproute backstage -n backstage`.

3. **Point `backstage.local` at the Traefik Gateway** (`bootstrap.sh`
   prints this command too):

   ```sh
   echo "$(kubectl get svc traefik -n kiac-gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}') backstage.local" | sudo tee -a /etc/hosts
   ```

4. **Sign in**: open `http://backstage.local` and confirm you're redirected
   to Entra ID and back after signing in.
