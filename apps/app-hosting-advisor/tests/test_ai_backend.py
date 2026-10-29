import asyncio
import json as json_module
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from azure.core.exceptions import AzureError
from fastapi import HTTPException
import httpx

from app import main
from app.advisor import (
    AIBackendThrottled,
    AIBackendUnavailable,
    AdvisorError,
    Session,
    interview,
)


def backend_response(status_code=200, content=None, headers=None):
    kwargs = {
        "status_code": status_code,
        "headers": headers,
        "request": httpx.Request("POST", "https://advisor.example/chat"),
    }
    if status_code == 200:
        kwargs["json"] = {
            "choices": [
                {
                    "message": {
                        "content": content
                        or '{"action":"ask","question":"Which port?"}'
                    }
                }
            ]
        }
    else:
        kwargs["json"] = {"error": {"message": "upstream diagnostic detail"}}
    return httpx.Response(**kwargs)


def backend_client(responses):
    client = MagicMock()
    client.post = AsyncMock(side_effect=responses)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    return client, manager


class AIBackendInferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_interview_uses_managed_identity_and_fixed_deployment(self):
        session = Session()
        credential = MagicMock()
        credential.get_token = Mock(return_value=SimpleNamespace(token="identity-token"))
        response = MagicMock()
        response.json.return_value = {
            "choices": [
                {"message": {"content": '{"action":"ask","question":"Which port?"}'}}
            ]
        }
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client_manager = MagicMock()
        client_manager.__aenter__ = AsyncMock(return_value=client)
        client_manager.__aexit__ = AsyncMock(return_value=None)

        with patch("app.advisor.httpx.AsyncClient", return_value=client_manager):
            result = await interview(
                session,
                "I have a containerized service.",
                "catalog",
                "https://advisor.services.ai.azure.com/",
                "fixed-deployment",
                credential,
            )

        self.assertEqual(result["action"], "ask")
        credential.get_token.assert_called_once_with("https://ai.azure.com/.default")
        args, kwargs = client.post.await_args
        self.assertEqual(
            args[0],
            "https://advisor.services.ai.azure.com/openai/v1/chat/completions",
        )
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer identity-token")
        self.assertEqual(kwargs["json"]["model"], "fixed-deployment")
        self.assertNotIn("api-key", kwargs["headers"])

    async def test_missing_backend_configuration_is_unavailable(self):
        with self.assertRaises(AIBackendUnavailable):
            await interview(Session(), "hello", "catalog", "", "", MagicMock())

    async def test_identity_authentication_failure_is_explicit(self):
        credential = MagicMock()
        credential.get_token = Mock(side_effect=AzureError("no credentials"))

        with self.assertRaises(AIBackendUnavailable):
            await interview(
                Session(),
                "hello",
                "catalog",
                "https://advisor.services.ai.azure.com",
                "fixed-deployment",
                credential,
            )

    async def test_retry_after_ms_retries_and_commits_the_turn_once(self):
        session = Session(
            messages=[
                {"role": "user", "content": "Existing request"},
                {"role": "assistant", "content": "What port?"},
            ]
        )
        client, manager = backend_client(
            [
                backend_response(429, headers={"retry-after-ms": "1000"}),
                backend_response(),
            ]
        )
        credential = MagicMock()

        with (
            patch("app.advisor.httpx.AsyncClient", return_value=manager),
            patch("app.advisor.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await interview(
                session,
                "8080",
                "catalog",
                "https://advisor.example",
                "fixed-deployment",
                credential,
            )

        self.assertEqual(result["action"], "ask")
        self.assertEqual(
            [message["content"] for message in session.messages],
            ["Existing request", "What port?", "8080", "Which port?"],
        )
        self.assertEqual(client.post.await_count, 2)
        sent_messages = client.post.await_args.kwargs["json"]["messages"]
        self.assertEqual(
            [message["content"] for message in sent_messages[1:]],
            ["Existing request", "What port?", "8080"],
        )
        sleep.assert_awaited_once_with(1.0)

    async def test_persistent_429_exhausts_bounded_retries_without_mutating_session(self):
        session = Session(messages=[{"role": "user", "content": "Earlier"}])
        original_messages = list(session.messages)
        client, manager = backend_client([backend_response(429) for _ in range(3)])

        with (
            patch("app.advisor.httpx.AsyncClient", return_value=manager),
            patch("app.advisor.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            with self.assertRaises(AIBackendThrottled) as error:
                await interview(
                    session,
                    "Try again",
                    "catalog",
                    "https://advisor.example",
                    "fixed-deployment",
                    MagicMock(),
                )

        self.assertEqual(client.post.await_count, 3)
        self.assertEqual(
            [awaited.args[0] for awaited in sleep.await_args_list],
            [0.5, 1.0],
        )
        self.assertEqual(session.messages, original_messages)
        self.assertIsNone(error.exception.retry_after_seconds)
        self.assertIn("temporarily rate-limiting", str(error.exception))
        self.assertIn("not added to the conversation", str(error.exception))
        self.assertNotIn("upstream diagnostic detail", str(error.exception))

    async def test_long_retry_after_returns_immediately_with_retry_guidance(self):
        client, manager = backend_client(
            [backend_response(429, headers={"Retry-After": "60"})]
        )
        session = Session()

        with (
            patch("app.advisor.httpx.AsyncClient", return_value=manager),
            patch("app.advisor.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            with self.assertRaises(AIBackendThrottled) as error:
                await interview(
                    session,
                    "Try again",
                    "catalog",
                    "https://advisor.example",
                    "fixed-deployment",
                    MagicMock(),
                )

        self.assertEqual(client.post.await_count, 1)
        sleep.assert_not_awaited()
        self.assertEqual(error.exception.retry_after_seconds, 60)
        self.assertIn("in about 60 seconds", str(error.exception))
        self.assertEqual(session.messages, [])

    async def test_non_429_http_errors_are_not_retried_and_do_not_mutate_history(self):
        for status_code in (401, 403, 500):
            with self.subTest(status_code=status_code):
                client, manager = backend_client(
                    [backend_response(status_code=status_code)]
                )
                session = Session(messages=[{"role": "user", "content": "Earlier"}])
                original_messages = list(session.messages)

                with (
                    patch("app.advisor.httpx.AsyncClient", return_value=manager),
                    patch("app.advisor.asyncio.sleep", new_callable=AsyncMock) as sleep,
                ):
                    with self.assertRaises(AIBackendUnavailable):
                        await interview(
                            session,
                            "Try again",
                            "catalog",
                            "https://advisor.example",
                            "fixed-deployment",
                            MagicMock(),
                        )

                self.assertEqual(client.post.await_count, 1)
                sleep.assert_not_awaited()
                self.assertEqual(session.messages, original_messages)

    async def test_invalid_model_result_does_not_commit_user_message(self):
        recommendation = {"pattern": "appservice"}
        session = Session(
            messages=[{"role": "assistant", "content": "Welcome"}],
            recommendation=recommendation,
        )
        original_messages = list(session.messages)
        _, manager = backend_client(
            [backend_response(content='{"action":"ask","question":"  "}')],
        )

        with patch("app.advisor.httpx.AsyncClient", return_value=manager):
            with self.assertRaises(AdvisorError):
                await interview(
                    session,
                    "New answer",
                    "catalog",
                    "https://advisor.example",
                    "fixed-deployment",
                    MagicMock(),
                )

        self.assertEqual(session.messages, original_messages)
        self.assertIs(session.recommendation, recommendation)

    async def test_concurrent_turns_for_a_session_are_serialized(self):
        started = asyncio.Event()
        release_first = asyncio.Event()
        sent_messages = []

        async def post(_url, *, json, **_kwargs):
            sent_messages.append(json["messages"][1:])
            if len(sent_messages) == 1:
                started.set()
                await release_first.wait()
                question = "First follow-up"
            else:
                question = "Second follow-up"
            return backend_response(
                content=json_module.dumps({"action": "ask", "question": question}),
            )

        client, manager = backend_client([])
        client.post.side_effect = post
        session = Session()

        with patch("app.advisor.httpx.AsyncClient", return_value=manager):
            first = asyncio.create_task(
                interview(
                    session,
                    "First answer",
                    "catalog",
                    "https://advisor.example",
                    "fixed-deployment",
                    MagicMock(),
                )
            )
            await started.wait()
            second = asyncio.create_task(
                interview(
                    session,
                    "Second answer",
                    "catalog",
                    "https://advisor.example",
                    "fixed-deployment",
                    MagicMock(),
                )
            )
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(first, second)

        self.assertEqual(
            [message["content"] for message in sent_messages[1]],
            ["First answer", "First follow-up", "Second answer"],
        )
        self.assertEqual(
            [message["content"] for message in session.messages],
            [
                "First answer",
                "First follow-up",
                "Second answer",
                "Second follow-up",
            ],
        )


class AIBackendConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_requires_platform_configuration(self):
        with (
            patch.object(main, "ai_endpoint", ""),
            patch.object(main, "ai_deployment_name", ""),
        ):
            with self.assertRaises(HTTPException) as error:
                await main.readiness()

        self.assertEqual(error.exception.status_code, 503)

    async def test_readiness_succeeds_when_configured(self):
        with (
            patch.object(main, "ai_endpoint", "https://advisor.services.ai.azure.com"),
            patch.object(main, "ai_deployment_name", "fixed-deployment"),
        ):
            self.assertEqual(await main.readiness(), {"status": "ready"})

    def test_user_model_configuration_route_is_removed(self):
        paths = {route.path for route in main.app.routes}
        self.assertNotIn("/api/sessions/{session_id}/configure", paths)

    def test_ui_does_not_expose_model_configuration(self):
        html = (
            Path(__file__).parents[1] / "app/static/index.html"
        ).read_text()

        self.assertNotIn('id="provider"', html)
        self.assertNotIn('id="endpoint"', html)
        self.assertNotIn('id="apiKey"', html)
        self.assertNotIn("/configure", html)

    def test_ui_only_clears_message_after_success_and_shows_server_guidance(self):
        html = (Path(__file__).parents[1] / "app/static/index.html").read_text()
        handler = html.split('document.querySelector("#send").onclick', 1)[1]
        self.assertLess(
            handler.index('await request("/api/chat"'),
            handler.index("input.value = \"\""),
        )
        self.assertIn('line("error", error.message)', handler)
        self.assertIn("send.disabled = true", handler)
        self.assertIn("send.disabled = false", handler)

    async def test_chat_returns_retryable_429_with_retry_after_header(self):
        from uuid import uuid4

        session_id = uuid4()
        session = Session(github_access_token="github-token")
        throttled = AIBackendThrottled(retry_after_seconds=30)
        with (
            patch.object(main, "_get_session", return_value=session),
            patch.object(main, "interview", side_effect=throttled),
        ):
            with self.assertRaises(HTTPException) as error:
                await main.chat(
                    main.ChatRequest(sessionId=session_id, message="hello"),
                    advisor_session=str(session_id),
                )

        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers, {"Retry-After": "30"})
        self.assertIn("Your message was not added", error.exception.detail)
