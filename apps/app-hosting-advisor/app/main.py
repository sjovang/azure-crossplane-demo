from __future__ import annotations

import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from azure.identity import DefaultAzureCredential
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .advisor import AIBackendUnavailable, AdvisorError, Session, interview
from .catalog import load_catalog
from .github import GitHubError, GitHubOAuth, GitHubPublisher
from .render import RenderError, render_team


SESSION_TTL_SECONDS = 3600
SESSION_CLEANUP_INTERVAL_SECONDS = 60
MAX_SESSIONS = 1000

sessions: dict[UUID, Session] = {}
oauth_states: dict[str, UUID] = {}


def _delete_session(session_id: UUID) -> None:
    sessions.pop(session_id, None)
    for state, state_session_id in tuple(oauth_states.items()):
        if state_session_id == session_id:
            oauth_states.pop(state, None)


def _purge_expired_sessions(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for session_id, session in tuple(sessions.items()):
        if session.expires_at <= now:
            _delete_session(session_id)


def _get_session(session_id: UUID) -> Session | None:
    session = sessions.get(session_id)
    if session is not None and session.expires_at <= time.monotonic():
        _delete_session(session_id)
        return None
    return session


def _create_session() -> tuple[UUID, Session]:
    _purge_expired_sessions()
    if len(sessions) >= MAX_SESSIONS:
        raise HTTPException(
            503,
            "The advisor is at session capacity. Try again later.",
        )
    session_id = uuid4()
    session = Session(expires_at=time.monotonic() + SESSION_TTL_SECONDS)
    sessions[session_id] = session
    return session_id, session


async def _session_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
        _purge_expired_sessions()


@asynccontextmanager
async def lifespan(_: FastAPI):
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        ai_credential.close()


app = FastAPI(title="App hosting advisor", lifespan=lifespan)
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
ai_endpoint = os.getenv("AI_ENDPOINT", "").strip().rstrip("/")
ai_deployment_name = os.getenv("AI_DEPLOYMENT_NAME", "").strip()
ai_credential = DefaultAzureCredential()
static = Path(__file__).parent / "static"


@app.middleware("http")
async def purge_expired_sessions(request: Request, call_next):
    _purge_expired_sessions()
    return await call_next(request)


class ChatRequest(BaseModel):
    sessionId: UUID
    message: str = Field(min_length=1, max_length=8000)


class PublishRequest(BaseModel):
    sessionId: UUID
    teamName: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def readiness() -> dict[str, str]:
    if not ai_endpoint or not ai_deployment_name:
        raise HTTPException(503, "The AI backend is not configured.")
    return {"status": "ready"}


@app.post("/api/sessions")
async def create_session(response: Response) -> dict[str, UUID]:
    session_id, _ = _create_session()
    response.set_cookie(
        "advisor_session",
        str(session_id),
        httponly=True,
        secure=session_cookie_secure,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
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
        parsed_session_id = UUID(session_id or "")
    except ValueError as error:
        raise HTTPException(401, "Start a new advisor session.") from error
    session = _get_session(parsed_session_id)
    if session is None:
        raise HTTPException(401, "Start a new advisor session.")

    state = secrets.token_urlsafe(32)
    for existing_state, state_session_id in tuple(oauth_states.items()):
        if state_session_id == parsed_session_id:
            oauth_states.pop(existing_state, None)
    oauth_states[state] = parsed_session_id
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

    session = _get_session(oauth_session_id)
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
        session = _get_session(UUID(session_id))
    except ValueError:
        return {"authenticated": False, "sessionId": None}
    if session is None:
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
            _delete_session(UUID(session_id))
        except ValueError:
            pass
    response.delete_cookie("advisor_session")
    return {"authenticated": False}


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    advisor_session: Optional[str] = Cookie(
        default=None,
        alias="advisor_session",
    ),
) -> dict[str, object]:
    session = _get_session(request.sessionId)
    if session is None:
        raise HTTPException(404, "Session not found.")
    if str(request.sessionId) != advisor_session or not session.github_access_token:
        raise HTTPException(401, "Sign in with GitHub before using the advisor.")
    try:
        return await interview(
            session,
            request.message,
            catalog,
            ai_endpoint,
            ai_deployment_name,
            ai_credential,
        )
    except AIBackendUnavailable as error:
        raise HTTPException(503, str(error)) from error
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
    session = _get_session(request.sessionId)
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
