# App hosting advisor

This team deploys the experimental app hosting advisor from
`apps/app-hosting-advisor/`. The advisor requires users to sign in with GitHub,
interviews them, asks an OpenAI-compatible model to select from the repository's
Crossplane catalog, renders the selected manifests deterministically, and opens
a pull request as the authenticated user.

The runtime does not persist credentials. A model API key and GitHub OAuth
access token are held in backend memory for the current session only. Users
never paste a GitHub token into the interface.

## Operator setup

Create a GitHub OAuth App and set its authorization callback URL to:

```text
https://appadvisor.demo.liasis.dev/api/auth/github/callback
```

Create one namespaced Kubernetes Secret after registering the OAuth App. Do not
commit it:

```sh
kubectl --namespace app-hosting-advisor create secret generic \
  appadvisor-github-oauth \
  --from-literal=client-id='<oauth-client-id>' \
  --from-literal=client-secret='<oauth-client-secret>' \
  --dry-run=client -o yaml |
  kubectl apply -f -
```

`XKeyVaultSecret` copies both values into Azure Key Vault. `XAppService` uses
Key Vault references, and `XKeyVault` grants its system-assigned identity the
Key Vault Secrets User role. No manual App Service setting is required.

The OAuth App should use the `public_repo` scope for this public repository;
the signed-in user must still have permission to create branches and pull
requests. For a private repository, configure `GITHUB_OAUTH_SCOPE=repo`.
