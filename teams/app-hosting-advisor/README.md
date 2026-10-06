# App hosting advisor prototype

This experimental team provisions a small Azure-hosted Open WebUI instance that can act as the front end for a chat-based app-hosting advisor. The goal is to let a user describe an application, then have an LLM reason about the available Crossplane compositions and recommend the right Azure hosting pattern.

## Recommended runtime shape

The strongest default for this repo is:

- Front end: Open WebUI, exposed via the existing `XWebApplication` composition
- Model access: Azure OpenAI or another OpenAI-compatible endpoint behind the UI
- Optional local fallback: Ollama for offline prototyping or private evaluations
- Policy engine: the model should select from the repo's available resource patterns, not invent unsupported Azure resources

This keeps the interface simple while making the model output grounded in the same resource inventory the repo already exposes: `XResourceGroup`, `XAppService`, `XContainerApp`, `XDatabase`, `XSecurityGroup`, and `XWebApplication`.

## Recommended AI backend options

### 1. Azure OpenAI (best default for production prototypes)

Use Azure OpenAI as the model backend behind Open WebUI. This is the easiest option when the team wants a managed inference endpoint and a familiar OpenAI-compatible API. It also fits the repo's Azure-first posture and makes the recommendations easy to explain in demos.

### 2. OpenAI-compatible gateway with a model router

If the evaluator wants to test multiple models or swap vendors without changing the UI, run a small model gateway in front of the chosen provider. The prompt layer then stays constant while the model backend changes under the hood.

### 3. Ollama for local/private evaluation

Ollama is useful when the prototype must operate without external model endpoints. It is less suited to shared demos or teams that need enterprise logging and managed identity.

## Prompt structure

The model should receive a system prompt that defines a narrow but robust job:

- act as a cloud architecture assistant for Azure hosting decisions
- recommend only Azure hosting patterns that match the composition inventory in this repo
- ask for missing facts only when they materially affect the recommendation
- prefer simple, explainable architectures over highly custom setups
- return a clear recommendation, trade-offs, and next steps

A practical structure looks like this:

1. Context block
   - repository resource inventory
   - available patterns (App Service, Container App, database, Entra integration, networking)
   - user is asking for a hosting recommendation, not a generic app implementation
2. User profile block
   - workload type, traffic profile, data needs, auth, region, and compliance constraints
3. Decision policy block
   - prefer the smallest viable Azure footprint
   - call out where a composition is unavailable or uncertain
   - clearly separate required inputs from optional inputs
4. Output contract
   - recommended pattern
   - reason for choosing it
   - required follow-up questions if important inputs are still missing
   - risks and migration path

## Interview flow for missing input

Keep the questionnaire brief and opinionated. Ask only what changes the recommendation
.

1. What is the app's runtime and language?
2. Is it primarily API-driven, a web app, or a background job?
3. What is the expected user load and latency target?
4. Does it need a database, cache, or queue?
5. Does it need Microsoft Entra authentication or public access only?
6. Which Azure region should it run in?
7. Are there compliance, data residency, or budget constraints?

If the answer is still ambiguous, the assistant should ask a single next question instead of batching many open-ended prompts. That keeps the session conversational and helps the model to keep the recommendation grounded.

## Why this fits the repo

The repository already models a small, explainable hosting catalog. Open WebUI provides a good interface for telling the story to a user without adding a heavy custom app stack. The model can reason over the same compositions this repo uses, which keeps the recommendation aligned with what is actually deployable.

The team infrastructure in `teams/app-hosting-advisor/` is intentionally minimal: it creates a namespace and a hosted Open WebUI front end so the prototype can be tested immediately while the AI backend and prompt layer are evolved separately.
