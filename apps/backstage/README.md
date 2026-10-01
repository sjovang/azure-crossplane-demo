# Developer portal ([Backstage](https://backstage.io))

A plain Backstage app from `@backstage/create-app`, published at
<https://backstage.demo.liasis.dev>. It runs on Azure App Service, not in
the cluster: [`teams/developer-portal`](../../teams/developer-portal/) declares
an `XWebApplication` (App Service, PostgreSQL, Key Vault) with a custom domain
in the `demo.liasis.dev` DNS zone and a managed certificate.

## Develop

Requires Node.js 22 or 24 (Node 26 is unsupported) and Yarn via corepack.

```sh
corepack enable
yarn install
yarn start      # http://localhost:3000, in-memory SQLite
```

### Microsoft Entra ID sign-in

`XWebApplication` creates the developer portal's Entra application, enterprise
service principal, and client credential. It registers both the production
callback and `http://localhost:7007/api/auth/microsoft/handler/frame`.

Re-run the cluster bootstrap after pulling this change. It upgrades the
Crossplane service principal with the Microsoft Graph
`Application.ReadWrite.All` application permission and admin consent.

For local development, export the generated values from the cluster before
starting Backstage:

```sh
export AUTH_MICROSOFT_CLIENT_ID="$(
  kubectl -n developer-portal get xenterpriseapp devportal \
    -o jsonpath='{.status.clientId}'
)"
export AUTH_MICROSOFT_CLIENT_SECRET="$(
  kubectl -n developer-portal get secret devportal-entra-client-secret \
    -o jsonpath='{.data.value}' | base64 --decode
)"
export AUTH_MICROSOFT_TENANT_ID="$(
  kubectl -n crossplane-system get configmap azure-platform-config \
    -o jsonpath='{.data.tenantId}'
)"
```

Any user in the configured tenant can sign in; this demo does not require an
Entra group assignment or a pre-existing Backstage `User` entity. The client
credential rotates every 30 days, is copied into the environment's Key Vault,
and causes App Service to restart against the new version. Re-export the local
client secret after a rotation.

### Catalog discovery

The catalog's `github` provider (`app-config.yaml`) scans the public
`sjovang/azure-crossplane-demo` repo on GitHub for `teams/*/catalog-info.yaml`
(one `Group` + `System` per team) and `compositions/*/*/catalog-info.yaml`
(one `API` per Crossplane XRD). It needs a `GITHUB_TOKEN` env var set before
`yarn start`, otherwise GitHub's API rate-limits unauthenticated requests:

```sh
export GITHUB_TOKEN=<a GitHub PAT with public_repo scope>
yarn start
```

## Build

```sh
docker build -t backstage apps/backstage   # or: container build ...
```

`app-config.production.yaml` reads the App Service settings that
`XWebApplication` sets: `APPLICATION_URL`, `DATABASE_HOST`, `_USER`,
`_PASSWORD`, `_NAME`, and the `AUTH_MICROSOFT_*` values. Database plugins use
one schema each.

## Release

- Pull requests that touch `apps/backstage/` build the image.
- Pushes to `main` push `ghcr.io/sjovang/azure-crossplane-demo/backstage`
  and commit the `sha-<commit>` tag into `teams/developer-portal`.
- Renovate (root `renovate.json`) opens one grouped PR for each Backstage
  release, updating every `@backstage/*` package and `backstage.json`.

> [!IMPORTANT]
> Make the GHCR `backstage` package public once. App Service pulls it
> anonymously.

`package.json` pins `@yarnpkg/core` to 4.9.1 because 4.9.2 ships a broken
`got` patch dependency. Remove the resolution once a fixed release is out.

`dependenciesMeta` skips native builds of `tree-sitter*` (Swagger UI uses the
WebAssembly build in the browser) and the optional `cpu-features`; they fail
to compile in the image.
