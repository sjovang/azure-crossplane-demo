import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apis/azure/ai-backend"
FIXTURES = ROOT / "tests/azure/ai-backend"


class AIBackendCompositionTests(unittest.TestCase):
    def setUp(self):
        self.definition = yaml.safe_load((API / "definition.yaml").read_text())
        self.composition = yaml.safe_load((API / "composition.yaml").read_text())
        self.expected = yaml.safe_load((FIXTURES / "expected.yaml").read_text())

    def test_public_api_contract(self):
        schema = self.definition["spec"]["versions"][0]["schema"][
            "openAPIV3Schema"
        ]
        spec = schema["properties"]["spec"]
        status = schema["properties"]["status"]["properties"]

        self.assertEqual(self.definition["spec"]["scope"], "Namespaced")
        self.assertEqual(
            set(spec["required"]),
            {"name", "accountName", "resourceGroupRef", "appServiceRef", "deployment"},
        )
        self.assertEqual(
            set(spec["properties"]["deployment"]["required"]),
            {"name", "model"},
        )
        self.assertEqual(
            set(spec["properties"]["deployment"]["properties"]["model"]["required"]),
            {"name", "version"},
        )
        self.assertIn(
            "metadata.name",
            spec["properties"]["appServiceRef"]["properties"]["name"][
                "description"
            ],
        )
        self.assertEqual(
            set(status),
            {"id", "endpoint", "deploymentName"},
        )

    def test_resource_graph_uses_scoped_managed_identity_access(self):
        graph = self.composition["spec"]["pipeline"][0]["input"]
        resources = graph["resources"]
        by_id = {resource["id"]: resource for resource in resources}

        self.assertEqual(
            [resource["id"] for resource in resources],
            self.expected["resourceIds"],
        )
        self.assertEqual(graph["status"], self.expected["status"])
        self.assertNotIn("schema", str(graph["status"]))

        account = by_id["account"]["template"]["spec"]["forProvider"]
        self.assertEqual(account["kind"], "AIServices")
        self.assertFalse(account["localAuthEnabled"])
        self.assertEqual(
            account["resourceGroupName"],
            "rg-${schema.spec.resourceGroupRef.name}",
        )

        deployment = by_id["deployment"]["template"]["spec"]["forProvider"]
        self.assertEqual(
            deployment["cognitiveAccountId"],
            "${account.status.atProvider.id}",
        )
        self.assertEqual(
            deployment["versionUpgradeOption"],
            "NoAutoUpgrade",
        )

        role = by_id["appServiceRoleAssignment"]["template"]["spec"][
            "forProvider"
        ]
        self.assertEqual(
            role["principalId"],
            "${appService.status.identityPrincipalId}",
        )
        self.assertEqual(role["scope"], "${account.status.atProvider.id}")
        self.assertTrue(role["roleDefinitionId"].endswith(
            "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
        ))


if __name__ == "__main__":
    unittest.main()
