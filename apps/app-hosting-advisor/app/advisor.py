from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from azure.core.credentials import TokenCredential
from azure.core.exceptions import AzureError
import httpx


SYSTEM_PROMPT = """\
You are an application hosting advisor for one GitOps repository.

Use only the Crossplane kinds in CATALOG. Do not invent Azure services,
fields, or manifests. Ask exactly one concise question when information that
materially changes the hosting choice is missing.

Decision rules:
- Prefer XWebApplication for a web/API container that needs PostgreSQL,
  because it wires the application, database, credentials, and Key Vault.
- Prefer XAppService for a continuously running stateless web/API container.
- Prefer XContainerApp for a stateless container that benefits from
  scale-to-zero or burst scaling.
- The deployable image, listening port, Azure region, and size are required.
- Supported regions and images are user choices; never guess them.
- Use lowercase DNS-compatible names, at most 12 characters for
  XWebApplication.
- Do not ask users for cloud or GitHub credentials; the platform handles
  authorization outside the model conversation.

Return only one JSON object with this shape:
{
  "action": "ask" | "recommend",
  "question": "one question when action is ask",
  "recommendation": {
    "pattern": "webapplication" | "appservice" | "containerapp",
    "name": "short lowercase name",
    "image": "registry/image:tag",
    "port": 8080,
    "location": "Azure region",
    "size": "small" | "medium" | "large",
    "databaseSize": "small" | "medium" | "large",
    "entraIdAuth": false,
    "reasoning": "brief reason",
    "tradeoffs": ["brief tradeoff"]
  }
}

Omit recommendation when action is ask. Set databaseSize and entraIdAuth only
for webapplication. Do not include markdown.

CATALOG:
__CATALOG__
"""


@dataclass
class Session:
    messages: list[dict[str, str]] = field(default_factory=list)
    recommendation: dict[str, Any] | None = None
    github_access_token: str | None = None
    github_login: str | None = None
    expires_at: float = 0


class AdvisorError(Exception):
    pass


class AIBackendUnavailable(AdvisorError):
    pass


MAX_CONVERSATION_MESSAGES = 40
MAX_CONVERSATION_CHARS = 64 * 1024


def _append_message(session: Session, role: str, content: str) -> None:
    current_chars = sum(len(message["content"]) for message in session.messages)
    if (
        len(session.messages) >= MAX_CONVERSATION_MESSAGES
        or current_chars + len(content) > MAX_CONVERSATION_CHARS
    ):
        raise AdvisorError("Conversation limit reached. Start a new advisor session.")
    session.messages.append({"role": role, "content": content})


def _extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = content.removeprefix("```json").removeprefix("```")
        content = content.removesuffix("```").strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise AdvisorError("The model returned invalid JSON.") from error
    if not isinstance(value, dict):
        raise AdvisorError("The model response was not a JSON object.")
    return value


def validate_recommendation(value: dict[str, Any]) -> dict[str, Any]:
    pattern = value.get("pattern")
    if pattern not in {"webapplication", "appservice", "containerapp"}:
        raise AdvisorError("The model selected an unsupported hosting pattern.")

    required = {"name", "image", "port", "location", "size", "reasoning"}
    missing = sorted(required - value.keys())
    if missing:
        raise AdvisorError(f"The recommendation is missing: {', '.join(missing)}.")

    name = value["name"]
    if not isinstance(name, str) or not name.isascii():
        raise AdvisorError("The generated resource name is invalid.")
    if not name.replace("-", "").isalnum() or name.lower() != name:
        raise AdvisorError("The generated resource name is invalid.")
    if pattern == "webapplication" and len(name) > 12:
        raise AdvisorError("XWebApplication names must be at most 12 characters.")
    if value["size"] not in {"small", "medium", "large"}:
        raise AdvisorError("The generated application size is invalid.")
    if not isinstance(value["port"], int) or not 1 <= value["port"] <= 65535:
        raise AdvisorError("The generated application port is invalid.")
    if pattern == "webapplication":
        if value.get("databaseSize") not in {"small", "medium", "large"}:
            raise AdvisorError("The generated database size is invalid.")
    return value


async def interview(
    session: Session,
    user_message: str,
    catalog: str,
    endpoint: str,
    deployment: str,
    credential: TokenCredential,
) -> dict[str, Any]:
    if not endpoint or not deployment:
        raise AIBackendUnavailable("The AI backend is not configured.")

    _append_message(session, "user", user_message)
    url = f"{endpoint.rstrip('/')}/openai/v1/chat/completions"
    try:
        token = await asyncio.to_thread(
            credential.get_token,
            "https://ai.azure.com/.default",
        )
    except AzureError as error:
        raise AIBackendUnavailable(
            f"Could not authenticate to the AI backend ({type(error).__name__})."
        ) from error

    payload = {
        "model": deployment,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.replace("__CATALOG__", catalog),
            },
            *session.messages,
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token.token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise AIBackendUnavailable(
            f"The AI backend returned HTTP {error.response.status_code}."
        ) from error
    except httpx.HTTPError as error:
        raise AIBackendUnavailable(
            f"The AI backend request failed ({type(error).__name__})."
        ) from error

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise AdvisorError("The model returned an unexpected response.") from error

    result = _extract_json(content)
    action = result.get("action")
    if action == "ask":
        question = result.get("question")
        if not isinstance(question, str) or not question.strip():
            raise AdvisorError("The model did not provide its follow-up question.")
        _append_message(session, "assistant", question)
        return {"action": "ask", "message": question}
    if action != "recommend":
        raise AdvisorError("The model returned an unsupported action.")

    recommendation = validate_recommendation(result.get("recommendation", {}))
    session.recommendation = recommendation
    summary = (
        f"Recommend **{recommendation['pattern']}**: "
        f"{recommendation['reasoning']}"
    )
    _append_message(session, "assistant", summary)
    return {
        "action": "recommend",
        "message": summary,
        "recommendation": recommendation,
    }
