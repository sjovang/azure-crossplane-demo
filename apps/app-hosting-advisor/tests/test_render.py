import unittest

import yaml

from app.render import RenderError, render_team


class RenderTests(unittest.TestCase):
    def test_renders_web_application_team(self) -> None:
        files = render_team(
            "checkout",
            {
                "pattern": "webapplication",
                "name": "checkout",
                "image": "ghcr.io/example/checkout:v1",
                "port": 8080,
                "location": "swedencentral",
                "size": "small",
                "databaseSize": "small",
                "entraIdAuth": True,
                "reasoning": "The app needs PostgreSQL.",
            },
        )

        infrastructure = list(
            yaml.safe_load_all(files["teams/checkout/infrastructure.yaml"])
        )
        self.assertEqual(infrastructure[0]["kind"], "XWebApplication")
        self.assertTrue(infrastructure[0]["spec"]["entraIdAuth"]["enabled"])
        self.assertIn("teams/checkout/kustomization.yaml", files)

    def test_renders_container_app_with_resource_group(self) -> None:
        files = render_team(
            "worker",
            {
                "pattern": "containerapp",
                "name": "worker",
                "image": "ghcr.io/example/worker:v1",
                "port": 8080,
                "location": "swedencentral",
                "size": "small",
                "reasoning": "The app benefits from scaling.",
            },
        )
        documents = list(
            yaml.safe_load_all(files["teams/worker/infrastructure.yaml"])
        )
        self.assertEqual(
            [document["kind"] for document in documents],
            ["XResourceGroup", "XContainerApp"],
        )

    def test_rejects_invalid_team_name(self) -> None:
        with self.assertRaises(RenderError):
            render_team("Invalid Team", {})


if __name__ == "__main__":
    unittest.main()
