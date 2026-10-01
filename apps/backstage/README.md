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
yarn start      # http://localhost:3000, in-memory SQLite, guest login
```

## Build

```sh
docker build -t backstage apps/backstage   # or: container build ...
```

`app-config.production.yaml` reads the App Service settings that
`XWebApplication` sets: `APPLICATION_URL` and `DATABASE_HOST`, `_USER`,
`_PASSWORD`, `_NAME`. Plugins use one schema each in that database.

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
