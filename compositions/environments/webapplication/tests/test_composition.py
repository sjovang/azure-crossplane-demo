import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WebApplicationCompositionTests(unittest.TestCase):
    def setUp(self):
        self.xrd = yaml.safe_load((ROOT / "xrd.yaml").read_text())
        self.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())
        self.xr = yaml.safe_load((ROOT / "tests/xr.yaml").read_text())
        self.auth_xr = yaml.safe_load((ROOT / "tests/xr-auth.yaml").read_text())
        self.expected = yaml.safe_load((ROOT / "tests/expected.yaml").read_text())

    def test_public_api_contract(self):
        version = self.xrd["spec"]["versions"][0]
        spec = version["schema"]["openAPIV3Schema"]["properties"]["spec"]

        self.assertEqual(self.xrd["spec"]["scope"], "Namespaced")
        self.assertEqual(
            spec["required"],
            ["name", "image", "location", "appSize", "databaseSize"],
        )
        self.assertEqual(spec["properties"]["port"]["default"], 8080)
        self.assertEqual(
            spec["properties"]["databaseAdminUsername"]["default"], "pgadmin"
        )

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
            by_id["appService"]["template"]["spec"]["envVars"],
            self.expected["appSettings"],
        )
        self.assertTrue(
            by_id["appService"]["template"]["spec"]["systemAssignedIdentity"]
        )
        self.assertEqual(
            by_id["appService"]["template"]["spec"]["customDomain"],
            self.expected["customDomain"],
        )
        self.assertTrue(
            by_id["database"]["template"]["spec"]["allowAzureServices"]
        )
        self.assertEqual(
            by_id["keyVaultSecret"]["template"]["spec"]["forProvider"][
                "valueSecretRef"
            ]["name"],
            "${databaseCredentials.metadata.name}",
        )
        self.assertEqual(
            by_id["entraClientSecret"]["template"]["spec"]["forProvider"][
                "valueSecretRef"
            ],
            {
                "name": "${enterpriseAppConnectionSecret.metadata.name}",
                "key": "value",
            },
        )
        self.assertEqual(
            by_id["enterpriseApp"]["includeWhen"],
            ["${schema.spec.?entraIdAuth.?enabled.orValue(false)}"],
        )
        self.assertEqual(by_id["passwordGenerator"]["readyWhen"], ["${true}"])
        self.assertEqual(
            by_id["entraClientSecret"]["includeWhen"],
            ["${schema.spec.?entraIdAuth.?enabled.orValue(false)}"],
        )
        self.assertEqual(
            by_id["crossplaneKeyVaultRoleAssignment"]["template"]["spec"][
                "forProvider"
            ]["roleDefinitionId"],
            "/providers/Microsoft.Authorization/roleDefinitions/"
            "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",
        )
        self.assertEqual(
            by_id["keyVaultRoleAssignment"]["template"]["spec"]["forProvider"][
                "roleDefinitionId"
            ],
            "/providers/Microsoft.Authorization/roleDefinitions/"
            "4633458b-17de-408a-b874-0445c86b69e6",
        )
        for resource_id in (
            "crossplaneKeyVaultRoleAssignment",
            "keyVaultRoleAssignment",
        ):
            self.assertEqual(by_id[resource_id]["template"]["metadata"], {})
        self.assertEqual(
            by_id["azurePlatformConfig"]["externalRef"]["metadata"],
            {
                "name": "azure-platform-config",
                "namespace": "crossplane-system",
            },
        )

    def test_example_supplies_required_fields(self):
        required = self.xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["required"]
        self.assertTrue(all(field in self.xr["spec"] for field in required))
        self.assertTrue(all(field in self.auth_xr["spec"] for field in required))
        self.assertTrue(self.auth_xr["spec"]["entraIdAuth"]["enabled"])


if __name__ == "__main__":
    unittest.main()
