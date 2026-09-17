# Developer Portal (Backstage)

> [!NOTE]
> This is a base configuration only: Microsoft Entra ID (Azure AD) OIDC
> sign-in, no catalog integration with Crossplane resources yet, and no
> Ingress/Traefik exposure — access is via `kubectl port-forward`.

Architecture:

- This directory holds everything Backstage, split into the two
  team-folder-style subfolders used elsewhere in this repo:
  - [`app/`](app/) — the Backstage application source (a standard
    `create-app`-style monorepo: `packages/backend`, `packages/app`), its
    `app-config.yaml`/`app-config.production.yaml`, and its `Dockerfile`.
    This is the only application source code in the repo — everything else
    here is declarative manifests.
  - [`infra/`](infra/) — the Kubernetes `Namespace`, `Deployment`, and
    `Service` for Backstage, applied by the dependent `apps` Flux
    Kustomization (`clusters/dev/apps-kustomization.yaml`,
    `dependsOn: flux-system`, `path: ./clusters/dev/apps/backstage/infra`).
    Only `infra/` is ever applied by Flux — `app/` is not Kubernetes YAML.

## Prerequisites

- Node.js 20 or 22
- A container registry you can push to (e.g. GHCR, Docker Hub, ACR)
- Run [`infrastructure/bootstrap.sh`](../../../../infrastructure/bootstrap.sh)
  first (see the top-level [README.md](../../../../README.md)) — it already
  creates the `<SP_NAME>-backstage` Entra ID app registration (redirect URI
  `http://localhost:7007/api/auth/microsoft/handler/frame`, the least-privilege
  Microsoft Graph permissions Backstage needs, and admin consent) and applies
  its credentials as the `backstage-entra-secret` Kubernetes `Secret`. The
  top-level README's prerequisites table lists the exact Azure/Entra ID roles
  required to run it.

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
   ```

3. **Port-forward and sign in**:

   ```sh
   kubectl port-forward -n backstage svc/backstage 7007:7007
   ```

   Open `http://localhost:7007` and confirm you're redirected to Entra ID
   and back after signing in.
