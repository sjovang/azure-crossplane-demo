import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apis/azure/keyvault"
FIXTURES = ROOT / "tests/azure/keyvault"


class KeyVaultCompositionTests(unittest.TestCase):
    def setUp(self):
        self.definition = yaml.safe_load((API / "definition.yaml").read_text())
        self.composition = yaml.safe_load((API / "composition.yaml").read_text())
        self.expected = yaml.safe_load((FIXTURES / "expected.yaml").read_text())

    def test_public_api_contract(self):
        schema = self.definition["spec"]["versions"][0]["schema"][
            "openAPIV3Schema"
        ]
        spec = schema["properties"]["spec"]
        status = schema["properties"]["status"]

        self.assertEqual(self.definition["spec"]["scope"], "Namespaced")
        self.assertEqual(
            spec["required"],
            ["name", "resourceGroupRef", "vaultName"],
        )
        self.assertEqual(spec["properties"]["vaultName"]["minLength"], 3)
        self.assertEqual(spec["properties"]["vaultName"]["maxLength"], 24)
        self.assertEqual(
            spec["properties"]["vaultName"]["pattern"],
            "^[a-zA-Z][a-zA-Z0-9-]*[a-zA-Z0-9]$",
        )
        self.assertEqual(
            spec["properties"]["name"]["x-kubernetes-validations"][0]["rule"],
            "self == oldSelf",
        )
        self.assertIn("id", status["properties"])

    def test_resource_graph_snapshot(self):
        graph = self.composition["spec"]["pipeline"][0]["input"]
        resources = graph["resources"]
        by_id = {resource["id"]: resource for resource in resources}

        self.assertEqual(
            [resource["id"] for resource in resources],
            self.expected["resourceIds"],
        )
        self.assertEqual(graph["status"], self.expected["status"])
        self.assertEqual(
            by_id["crossplaneKeyVaultRoleAssignment"]["template"]["spec"][
                "forProvider"
            ]["roleDefinitionId"],
            "/subscriptions/${azurePlatformConfig.data.subscriptionId}"
            "/providers/Microsoft.Authorization/roleDefinitions/"
            "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",
        )
        self.assertEqual(
            by_id["appServiceKeyVaultRoleAssignment"]["template"]["spec"][
                "forProvider"
            ]["roleDefinitionId"],
            "/subscriptions/${azurePlatformConfig.data.subscriptionId}"
            "/providers/Microsoft.Authorization/roleDefinitions/"
            "4633458b-17de-408a-b874-0445c86b69e6",
        )
        self.assertEqual(
            by_id["appService"]["includeWhen"],
            ['${schema.spec.?appServiceRef.?name.orValue("") != ""}'],
        )


if __name__ == "__main__":
    unittest.main()
