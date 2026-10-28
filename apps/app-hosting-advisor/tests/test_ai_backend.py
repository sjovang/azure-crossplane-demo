import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from azure.core.exceptions import AzureError
from fastapi import HTTPException

from app import main
from app.advisor import AIBackendUnavailable, Session, interview


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
