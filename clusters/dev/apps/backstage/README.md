# Developer Portal (Backstage)

> [!NOTE]
> This is a base configuration only: Microsoft Entra ID (Azure AD) OIDC
> sign-in, no catalog integration with Crossplane resources yet. Backstage
> is exposed through the kiac cluster's Gateway API addon (Traefik), not
> `kubectl port-forward`, at a hostname you choose — see
> [Choosing how to reach Backstage](#choosing-how-to-reach-backstage).

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
    `apps` Flux Kustomization (`clusters/dev/flux-kustomizations/apps/`,
    `dependsOn: flux-system`, `path: ./clusters/dev/apps/backstage/infra`).
    Only `infra/` is ever applied by Flux — `app/` is not Kubernetes YAML.
- [`infra/httproute.yaml`](infra/httproute.yaml) attaches Backstage to the
  `kiac` `Gateway` (namespace `kiac-gateway`, `GatewayClass traefik`) that
  [kiac's gateway addon](https://github.com/saiyam1814/kiac/blob/main/examples/gateway-api-lab.md)
  pre-creates on cluster creation
  (`infrastructure/config.yaml`'s `addons.gateway: true`), at the hostname
  `https://${BACKSTAGE_HOSTNAME}`. No Ingress/`type: LoadBalancer` Service is
  created directly for Backstage; Traefik's own `LoadBalancer` Service in
  `kiac-gateway` is the single entry point shared by every `HTTPRoute` on
  the cluster. Bootstrap creates a self-signed certificate for the hostname,
  adds an HTTPS listener to the kiac Gateway, and redirects HTTP to HTTPS.
- `${BACKSTAGE_HOSTNAME}` in `infra/httproute.yaml` and
  `infra/deployment.yaml` is resolved by the `apps` Flux Kustomization's
  `spec.postBuild.substituteFrom` (see
  [`clusters/dev/flux-kustomizations/apps/`](../../flux-kustomizations/apps/)),
  reading from a `backstage-vars` `ConfigMap` in `flux-system`. That
  `ConfigMap` is never committed to git — it's applied imperatively by
  [`infrastructure/set-backstage-hostname.sh`](../../../../infrastructure/set-backstage-hostname.sh),
  the same pattern as the `azure-secret`/`backstage-entra-secret` `Secret`s.

## Prerequisites

- The `container` CLI (already a repo prerequisite, see the top-level
  [README.md](../../../../README.md)) — no container registry, in-cluster or
  external, is needed
- Run [`infrastructure/bootstrap.sh`](../../../../infrastructure/bootstrap.sh)
  (see the top-level [README.md](../../../../README.md)) with
  `BACKSTAGE_ENABLED=true` — it creates or reuses the kiac
  cluster, builds the Backstage image (`container build`) and loads it into
  every node (`kiac load image`, no registry involved), creates the
  `<SP_NAME>-backstage` Entra ID app registration (the least-privilege
  Microsoft Graph permissions Backstage needs, and admin consent), applies
  its credentials as the `backstage-entra-secret` Kubernetes `Secret`,
  configures how Backstage is reached (see below), bootstraps Flux against
  `clusters/dev-with-backstage`, and lets GitOps deploy the Backstage Flux
  Kustomization. The top-level README's prerequisites table lists the exact
  Azure/Entra ID roles required to run it. Backstage is disabled by default;
  an unset or false value uses the `clusters/dev` root and skips it.

## Choosing how to reach Backstage

Set `BACKSTAGE_HOSTNAME` before running `bootstrap.sh` (or
`set-backstage-hostname.sh` standalone, see below) to pick one of two
options. Both keep the Entra app registration's redirect URI and the
`backstage-vars` `ConfigMap` in sync automatically — the only difference is
what you have to do outside this repo to make the hostname resolve.

| | `backstage.local` (default) | Your own domain |
| --- | --- | --- |
| Set with | nothing, or `BACKSTAGE_HOSTNAME=backstage.local` | `BACKSTAGE_HOSTNAME=backstage.example.com` |
| What you must do yourself | Run the printed `sudo ... >> /etc/hosts` command | Create/update a DNS `A` record yourself, pointed at the Traefik LoadBalancer IP |
| Touches your machine? | Yes — one `sudo` edit to `/etc/hosts` | No |
| Survives a `teardown.sh` + `bootstrap.sh` cycle? | No — the LoadBalancer IP can change, requiring a new `/etc/hosts` line | No — same reason, requires updating the DNS record |

Neither option is scripted end-to-end: writing to `/etc/hosts` needs `sudo`
(this repo never runs `sudo` for you), and DNS records live with a
third-party provider this repo has no integration with. Both options print
the exact next step to take.

Use [`infrastructure/set-backstage-hostname.sh`](../../../../infrastructure/set-backstage-hostname.sh)
standalone if you want to switch hostnames later without re-running all of
`bootstrap.sh`:

```sh
BACKSTAGE_HOSTNAME=backstage.example.com ./infrastructure/set-backstage-hostname.sh
```

## Steps

1. Run `infrastructure/bootstrap.sh` (see the top-level README), setting
   `BACKSTAGE_HOSTNAME` first if you want the public-DNS option, then
   follow the `/etc/hosts` or DNS instructions it prints.
2. Once Flux has converged (`flux get kustomizations -A`), open
  `https://$BACKSTAGE_HOSTNAME`, accept the self-signed certificate warning,
  and sign in with Microsoft Entra ID.

Re-running `infrastructure/bootstrap.sh` after changing the app source under
[`app/`](app/) rebuilds and reloads the image, then you only need:

```sh
kubectl rollout restart deployment/backstage -n backstage
```

so the Deployment picks up the freshly loaded image (fixed tag
`backstage:dev`, `imagePullPolicy: Never` — see
[`infra/deployment.yaml`](infra/deployment.yaml)).

### Troubleshooting

- `flux get kustomizations -A` shows the `apps` Kustomization; `Ready=True`
  means the Deployment/Service/HTTPRoute were applied.
- `kubectl get httproute backstage -n backstage -o wide` /
  `kubectl describe httproute backstage -n backstage` should show an
  attached `Gateway` and `Accepted=True`/`ResolvedRefs=True`. If the
  hostname shown is the literal string `${BACKSTAGE_HOSTNAME}`, the
  `backstage-vars` `ConfigMap` hadn't been applied yet when Flux last
  reconciled — run `set-backstage-hostname.sh` and
  `flux reconcile kustomization apps -n flux-system --with-source`.
- `kubectl get svc traefik -n kiac-gateway` shows the current Traefik
  LoadBalancer IP, useful for either the `/etc/hosts` command or your DNS
  record.
- `kubectl get configmap backstage-vars -n flux-system -o yaml` shows the
  hostname Flux is currently substituting in.

