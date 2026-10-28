# App hosting advisor

This experiment uses a purpose-built chat UI rather than stock Open WebUI.
Open WebUI is a strong generic interface for OpenAI-compatible endpoints, but
the advisor also needs a constrained interview state machine, deterministic
manifest rendering, and a GitHub credential boundary. Keeping those operations
in one small service ensures the model never receives the GitHub access token.

## Flow

1. The service uses its centrally configured Azure AI Services endpoint and
   deployment. App Service managed identity obtains an Entra token for
   inference; users cannot select a provider, model, endpoint, or credential.
2. The model receives the checked-in XRD schemas as its allowed catalog.
3. It asks one follow-up question at a time until it can select
   `XWebApplication`, `XAppService`, or `XContainerApp`.
4. Application code validates the structured recommendation and renders YAML;
   the model never writes repository files directly.
5. The user signs in with GitHub. The service uses the authenticated session to
   create a branch, write `teams/<name>/`, update `teams/kustomization.yaml`,
   and open a PR.

The advisor does not use or accept a model API key. The App Service managed
identity receives the `Cognitive Services OpenAI User` role on the AI account.
The browser never receives or submits a GitHub token. The GitHub OAuth access
token is held only in process memory. This is appropriate for an experiment; a
production version should use a GitHub App user authorization flow and
persistent encrypted session storage.

Advisor sessions expire after one hour, are periodically removed from process
memory, and are limited to 1,000 concurrent sessions. Conversation history is
limited to 40 messages and 64 KiB per session.

The deployment provides `AI_ENDPOINT` and `AI_DEPLOYMENT_NAME`; `/ready`
returns HTTP 503 until both are set. For local development, set these values
and authenticate through `DefaultAzureCredential` (for example, with Azure
CLI credentials). The production App Service receives them from its
infrastructure configuration.

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
AI_ENDPOINT="https://YOUR_ACCOUNT.services.ai.azure.com" \
AI_DEPLOYMENT_NAME="YOUR_DEPLOYMENT" \
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
