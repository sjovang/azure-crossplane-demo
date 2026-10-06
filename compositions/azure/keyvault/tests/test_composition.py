import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class KeyVaultCompositionTests(unittest.TestCase):
    def setUp(self):
        self.xrd = yaml.safe_load((ROOT / "xrd.yaml").read_text())
        self.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())
        self.expected = yaml.safe_load((ROOT / "tests/expected.yaml").read_text())

    def test_public_api_contract(self):
        version = self.xrd["spec"]["versions"][0]
        spec = version["schema"]["openAPIV3Schema"]["properties"]["spec"]
        status = version["schema"]["openAPIV3Schema"]["properties"]["status"]

        self.assertEqual(self.xrd["spec"]["scope"], "Namespaced")
        self.assertEqual(
            spec["required"],
            ["name", "vaultName", "resourceGroupRef"],
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
