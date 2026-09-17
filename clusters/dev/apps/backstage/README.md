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
- An Azure account with permission to create an Entra ID App Registration

## Steps

1. **Create an Entra ID App Registration** (manual, in the
   [Azure Portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)):

   - Add a **Web** platform redirect URI:
     `http://localhost:7007/api/auth/microsoft/handler/frame` (this base
     config targets `kubectl port-forward`, not an Ingress hostname).
   - Under **API permissions**, add the `Microsoft Graph` delegated
     permissions: `email`, `offline_access`, `openid`, `profile`,
     `User.Read`. Grant admin consent if your tenant requires it.
   - Under **Certificates & secrets**, create a client secret and note its
     value.
   - Note the **Application (client) ID** and **Directory (tenant) ID** from
     the App Registration's Overview page.

2. **Build and push the Backstage image** (from this directory's
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

3. **Create the `backstage-entra-secret` Secret** in the cluster, using the
   values from step 1 (idempotent, like the `azure-secret` created by
   `bootstrap.sh`):

   ```sh
   kubectl create namespace backstage --dry-run=client -o yaml | kubectl apply -f -
   kubectl create secret generic backstage-entra-secret -n backstage \
     --from-literal=clientId=<application-client-id> \
     --from-literal=clientSecret=<client-secret-value> \
     --from-literal=tenantId=<directory-tenant-id> \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

4. **Reconcile Flux and verify**:

   ```sh
   flux reconcile kustomization apps -n flux-system --with-source
   flux get kustomizations -A
   ```

5. **Port-forward and sign in**:

   ```sh
   kubectl port-forward -n backstage svc/backstage 7007:7007
   ```

   Open `http://localhost:7007` and confirm you're redirected to Entra ID
   and back after signing in.
