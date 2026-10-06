# App hosting advisor

This experiment uses a purpose-built chat UI rather than stock Open WebUI.
Open WebUI is a strong generic interface for OpenAI-compatible endpoints, but
the advisor also needs a constrained interview state machine, deterministic
manifest rendering, and a GitHub credential boundary. Keeping those operations
in one small service ensures the model never receives either the model API key
or GitHub token.

## Flow

1. The user configures Azure OpenAI or another OpenAI-compatible endpoint.
2. The model receives the checked-in XRD schemas as its allowed catalog.
3. It asks one follow-up question at a time until it can select
   `XWebApplication`, `XAppService`, or `XContainerApp`.
4. Application code validates the structured recommendation and renders YAML;
   the model never writes repository files directly.
5. The user signs in with GitHub. The service uses the authenticated session to
   create a branch, write `teams/<name>/`, update `teams/kustomization.yaml`,
   and open a PR.

The model API key and GitHub OAuth access token are held only in process memory.
The browser never receives or submits a GitHub token. This is appropriate for
an experiment; a production version should use a GitHub App user authorization
flow and persistent encrypted session storage.

Azure OpenAI hostnames are allowed automatically. Other OpenAI-compatible
hosts must be listed in the deployment's comma-separated
`ALLOWED_MODEL_HOSTS` environment variable. This prevents users from turning
the model connection into an arbitrary server-side request.

## Backend options considered

- **Azure OpenAI**: best fit for the Azure demo and supports managed identity
  in a production design.
- **OpenAI-compatible endpoint**: implemented to allow model gateways and
  local servers without changing the interview protocol.
- **Open WebUI plus a Pipe/Function**: viable for generic chat, but weaker for
  this experiment because PR authorization and deterministic file generation
  still require a separate trusted service.

References:

- [Open WebUI OpenAI-compatible connections](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/)
- [Azure OpenAI keyless authentication](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/managed-identity)
- [GitHub REST API: repository contents](https://docs.github.com/rest/repos/contents)
- [GitHub REST API: pull requests](https://docs.github.com/rest/pulls/pulls)
- [GitHub OAuth app web application flow](https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)

## Local run

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r apps/app-hosting-advisor/requirements.txt
COMPOSITIONS_PATH=compositions \
GITHUB_OAUTH_CLIENT_ID=... \
GITHUB_OAUTH_CLIENT_SECRET=... \
GITHUB_OAUTH_REDIRECT_URI=http://localhost:8080/api/auth/github/callback \
SESSION_COOKIE_SECURE=false \
  uvicorn app.main:app --app-dir apps/app-hosting-advisor --port 8080
```

Register a GitHub OAuth App with the callback URL configured in
`GITHUB_OAUTH_REDIRECT_URI`. For this public repository, the default
`public_repo` scope is sufficient for users who can create branches and pull
requests. Use `repo` only if the deployment must target private repositories.
