from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
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
    interview_lock: asyncio.Lock | None = field(default=None, repr=False)


class AdvisorError(Exception):
    pass


class AIBackendUnavailable(AdvisorError):
    pass


class AIBackendThrottled(AIBackendUnavailable):
    def __init__(self, retry_after_seconds: int | None = None):
        self.retry_after_seconds = retry_after_seconds
        retry_guidance = (
            f" Please try again in about {retry_after_seconds} seconds."
            if retry_after_seconds is not None
            else " Please try again shortly."
        )
        super().__init__(
            "The AI service is temporarily rate-limiting requests. "
            "Your message was not added to the conversation."
            + retry_guidance
        )


MAX_CONVERSATION_MESSAGES = 40
MAX_CONVERSATION_CHARS = 64 * 1024
MAX_AI_429_RETRIES = 2
MAX_AI_RETRY_WAIT_SECONDS = 3.0


def _append_message(session: Session, role: str, content: str) -> None:
    current_chars = sum(len(message["content"]) for message in session.messages)
    if (
        len(session.messages) >= MAX_CONVERSATION_MESSAGES
        or current_chars + len(content) > MAX_CONVERSATION_CHARS
    ):
        raise AdvisorError("Conversation limit reached. Start a new advisor session.")
    session.messages.append({"role": role, "content": content})


def _retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after_ms = response.headers.get("retry-after-ms")
    if retry_after_ms is not None:
        try:
            seconds = float(retry_after_ms) / 1000
        except ValueError:
            pass
        else:
            if isfinite(seconds):
                return max(0.0, seconds)

    retry_after = response.headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        seconds = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if isfinite(seconds) else None


async def _post_with_throttling(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
) -> httpx.Response:
    retry_waited = 0.0
    for attempt in range(MAX_AI_429_RETRIES + 1):
        response = await client.post(url, headers=headers, json=json)
        try:
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429:
                raise

            server_delay = _retry_after_seconds(error.response)
            retry_after = ceil(server_delay) if server_delay is not None else None
            if attempt >= MAX_AI_429_RETRIES:
                raise AIBackendThrottled(retry_after) from error

            delay = server_delay if server_delay is not None else 0.5 * (2**attempt)
            remaining_wait = MAX_AI_RETRY_WAIT_SECONDS - retry_waited
            if delay > remaining_wait:
                raise AIBackendThrottled(retry_after) from error
            await asyncio.sleep(delay)
            retry_waited += delay

    raise AssertionError("The bounded AI retry loop exited without a response.")


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


async def _interview_mutating(
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
            response = await _post_with_throttling(
                client,
                url,
                headers={
                    "Authorization": f"Bearer {token.token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
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


async def interview(
    session: Session,
    user_message: str,
    catalog: str,
    endpoint: str,
    deployment: str,
    credential: TokenCredential,
) -> dict[str, Any]:
    lock = session.interview_lock
    if lock is None:
        lock = session.interview_lock = asyncio.Lock()
    async with lock:
        pending = Session(
            messages=[dict(message) for message in session.messages],
            recommendation=session.recommendation,
        )
        answer = await _interview_mutating(
            pending,
            user_message,
            catalog,
            endpoint,
            deployment,
            credential,
        )
        session.messages = pending.messages
        session.recommendation = pending.recommendation
        return answer
