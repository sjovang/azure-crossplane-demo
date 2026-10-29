# App hosting advisor

This team deploys the experimental app hosting advisor from
`apps/app-hosting-advisor/`. The advisor requires users to sign in with GitHub,
interviews them, asks an OpenAI-compatible model to select from the repository's
Crossplane catalog, renders the selected manifests deterministically, and opens
a pull request as the authenticated user.

The backend uses the App Service's managed identity to access the Azure AI
backend; users do not supply a model API key. GitHub OAuth access tokens are
held in backend memory for the current session only. Users never paste a
GitHub token into the interface.

## Operator setup

### Register the GitHub OAuth App

OAuth App registration, callback configuration, and client-secret generation
must be done in GitHub's browser settings. There is no supported `gh` command
or GitHub API for registering this OAuth App. On macOS, open registration with:

```sh
open 'https://github.com/settings/applications/new'
```

The app is already registered with client ID `Ov23lizwbt9OQknZcGRW` (a public
identifier, not a secret). For this deployment, edit that existing OAuth App
under **Settings > Developer settings > OAuth Apps** rather than registering
a duplicate. Use these settings:

| Setting | Value |
| --- | --- |
| Application name | App Hosting Advisor |
| Homepage URL | `https://appadvisor.demo.liasis.dev` |
| Authorization callback URL | `https://appadvisor.demo.liasis.dev/api/auth/github/callback` |

The authorization callback URL is the OAuth redirect URI and must match
`GITHUB_OAUTH_REDIRECT_URI` in `infrastructure.yaml`.

Keep **Expire user access tokens** enabled: GitHub access tokens expire after
8 hours, while advisor sessions expire 1 hour after creation. The current backend
does not refresh tokens; users start a new session and sign in again after
session expiry.

On the OAuth App's settings page, select **Generate a new client secret**.
Never commit the secret or paste it into chat.

### Store the OAuth credentials in Kubernetes

Select the intended `kubectl` context and wait for the `app-hosting-advisor`
namespace to exist. These are Kubernetes credentials, not GitHub Actions
secrets: do not use `gh secret set`.

Run from the repository root (including from zsh on macOS):

```sh
./teams/app-hosting-advisor/setup-github-oauth.sh
```

The script runs in Bash, disables tracing, and prompts with hidden input so
the secret stays out of shell history and command arguments. It rejects empty
input and idempotently creates or updates the namespaced Secret.

`XKeyVaultSecret` copies both values into Azure Key Vault. `XAppService` uses
Key Vault references, and `XKeyVault` grants its system-assigned identity the
Key Vault Secrets User role. No manual App Service setting is required.

The OAuth App should use the `public_repo` scope for this public repository;
the signed-in user must still have permission to create branches and pull
requests. For a private repository, configure `GITHUB_OAUTH_SCOPE=repo`.
