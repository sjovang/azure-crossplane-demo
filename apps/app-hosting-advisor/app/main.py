from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .advisor import AdvisorError, ModelConfig, Session, interview
from .catalog import load_catalog
from .github import GitHubError, GitHubOAuth, GitHubPublisher
from .render import RenderError, render_team


app = FastAPI(title="App hosting advisor")
sessions: dict[UUID, Session] = {}
oauth_states: dict[str, UUID] = {}
catalog = load_catalog(Path(os.getenv("COMPOSITIONS_PATH", "/app/compositions")))
repository = os.getenv("GITHUB_REPOSITORY", "sjovang/azure-crossplane-demo")
github_client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
github_client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
github_redirect_uri = os.getenv(
    "GITHUB_OAUTH_REDIRECT_URI",
    "http://localhost:8080/api/auth/github/callback",
)
github_oauth_scope = os.getenv("GITHUB_OAUTH_SCOPE", "public_repo")
session_cookie_secure = os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"
allowed_model_hosts = {
    host.strip().lower()
    for host in os.getenv("ALLOWED_MODEL_HOSTS", "").split(",")
    if host.strip()
}
static = Path(__file__).parent / "static"


class ConfigureRequest(BaseModel):
    provider: str
    endpoint: str
    model: str
    apiKey: str
    apiVersion: str = "2024-10-21"


class ChatRequest(BaseModel):
    sessionId: UUID
    message: str = Field(min_length=1, max_length=8000)


class PublishRequest(BaseModel):
    sessionId: UUID
    teamName: str


def validate_endpoint(provider: str, endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(400, "The model endpoint must be an HTTPS URL.")
    hostname = parsed.hostname.lower()
    if provider == "azure":
        suffixes = (".openai.azure.com", ".services.ai.azure.com")
        if not hostname.endswith(suffixes):
            raise HTTPException(400, "The Azure endpoint hostname is invalid.")
    elif hostname not in {"api.openai.com", *allowed_model_hosts}:
        raise HTTPException(
            400,
            "This model host is not allowlisted by the service operator.",
        )
    return endpoint.rstrip("/")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sessions")
async def create_session(response: Response) -> dict[str, UUID]:
    session_id = uuid4()
    sessions[session_id] = Session()
    response.set_cookie(
        "advisor_session",
        str(session_id),
        httponly=True,
        secure=session_cookie_secure,
        samesite="lax",
        max_age=3600,
    )
    return {"sessionId": session_id}


@app.get("/api/auth/github")
async def github_login(
    session_id: Optional[str] = Cookie(default=None, alias="advisor_session"),
) -> RedirectResponse:
    if not github_client_id or not github_client_secret:
        raise HTTPException(
            503,
            "GitHub OAuth is not configured by the service operator.",
        )
    try:
        session = sessions[UUID(session_id or "")]
    except (KeyError, ValueError) as error:
        raise HTTPException(401, "Start a new advisor session.") from error

    state = secrets.token_urlsafe(32)
    oauth_states[state] = UUID(session_id)
    from urllib.parse import urlencode

    params = urlencode(
        {
            "client_id": github_client_id,
            "redirect_uri": github_redirect_uri,
            "scope": github_oauth_scope,
            "state": state,
        }
    )
    return RedirectResponse(
        url=f"https://github.com/login/oauth/authorize?{params}",
        status_code=307,
    )


@app.get("/api/auth/github/callback")
async def github_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    cookie_session_id: Optional[str] = Cookie(
        default=None,
        alias="advisor_session",
    ),
):
    oauth_session_id = oauth_states.pop(state or "", None)
    if not code or oauth_session_id is None or cookie_session_id is None:
        raise HTTPException(400, "Invalid GitHub OAuth callback.")
    try:
        cookie_uuid = UUID(cookie_session_id)
    except ValueError as error:
        raise HTTPException(400, "Invalid advisor session.") from error
    if oauth_session_id != cookie_uuid:
        raise HTTPException(400, "GitHub OAuth session mismatch.")
    if not github_client_id or not github_client_secret:
        raise HTTPException(503, "GitHub OAuth is not configured.")

    try:
        token, login = await GitHubOAuth(
            github_client_id,
            github_client_secret,
            github_redirect_uri,
        ).exchange_code(code)
    except GitHubError as error:
        raise HTTPException(400, str(error)) from error

    session = sessions.get(oauth_session_id)
    if session is None:
        raise HTTPException(401, "Your advisor session has expired.")
    session.github_access_token = token
    session.github_login = login
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/auth/me")
async def github_me(
    session_id: Optional[str] = Cookie(default=None, alias="advisor_session"),
) -> dict[str, object]:
    if not session_id:
        return {"authenticated": False, "sessionId": None}
    try:
        session = sessions[UUID(session_id)]
    except (KeyError, ValueError):
        return {"authenticated": False, "sessionId": None}
    return {
        "authenticated": bool(session.github_access_token),
        "login": session.github_login,
        "sessionId": session_id,
    }


@app.post("/api/auth/logout")
async def github_logout(
    response: Response,
    session_id: Optional[str] = Cookie(default=None, alias="advisor_session"),
) -> dict[str, bool]:
    if session_id:
        try:
            sessions.pop(UUID(session_id), None)
        except ValueError:
            pass
    response.delete_cookie("advisor_session")
    return {"authenticated": False}


@app.post("/api/sessions/{session_id}/configure")
async def configure(
    session_id: UUID,
    request: ConfigureRequest,
    advisor_session: Optional[str] = Cookie(
        default=None,
        alias="advisor_session",
    ),
) -> dict[str, str]:
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found.")
    if str(session_id) != advisor_session or not session.github_access_token:
        raise HTTPException(401, "Sign in with GitHub before using the advisor.")
    if request.provider not in {"azure", "openai"}:
        raise HTTPException(400, "Provider must be azure or openai.")
    session.config = ModelConfig(
        provider=request.provider,
        endpoint=validate_endpoint(request.provider, request.endpoint),
        model=request.model,
        api_key=request.apiKey,
        api_version=request.apiVersion,
    )
    return {"status": "configured"}


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    advisor_session: Optional[str] = Cookie(
        default=None,
        alias="advisor_session",
    ),
) -> dict[str, object]:
    session = sessions.get(request.sessionId)
    if session is None:
        raise HTTPException(404, "Session not found.")
    if str(request.sessionId) != advisor_session or not session.github_access_token:
        raise HTTPException(401, "Sign in with GitHub before using the advisor.")
    try:
        return await interview(session, request.message, catalog)
    except AdvisorError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/publish")
async def publish(
    request: PublishRequest,
    advisor_session: Optional[str] = Cookie(
        default=None,
        alias="advisor_session",
    ),
) -> dict[str, str]:
    session = sessions.get(request.sessionId)
    if session is None:
        raise HTTPException(404, "Session not found.")
    if str(request.sessionId) != advisor_session or not session.github_access_token:
        raise HTTPException(401, "Sign in with GitHub before using the advisor.")
    if session.recommendation is None:
        raise HTTPException(400, "Complete the interview before publishing.")
    try:
        files = render_team(request.teamName, session.recommendation)
        async with GitHubPublisher(
            repository,
            session.github_access_token,
        ) as publisher:
            url = await publisher.publish(
                request.teamName,
                files,
                session.recommendation["reasoning"],
            )
    except (RenderError, GitHubError) as error:
        raise HTTPException(400, str(error)) from error
    return {"pullRequestUrl": url}
