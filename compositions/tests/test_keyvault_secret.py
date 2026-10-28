import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apis/azure/keyvault-secret"
FIXTURES = ROOT / "tests/azure/keyvault-secret"


class KeyVaultSecretCompositionTests(unittest.TestCase):
    def setUp(self):
        self.definition = yaml.safe_load((API / "definition.yaml").read_text())
        self.composition = yaml.safe_load((API / "composition.yaml").read_text())
        self.expected = yaml.safe_load((FIXTURES / "expected.yaml").read_text())

    def test_public_api_contract(self):
        schema = self.definition["spec"]["versions"][0]["schema"][
            "openAPIV3Schema"
        ]

        self.assertEqual(self.definition["spec"]["scope"], "Namespaced")
        self.assertEqual(
            schema["properties"]["spec"]["required"],
            ["keyVaultRef", "name", "valueSecretRef"],
        )
        self.assertIn("id", schema["properties"]["status"]["properties"])
        self.assertIn(
            "versionlessId",
            schema["properties"]["status"]["properties"],
        )
        self.assertEqual(
            schema["properties"]["spec"]["properties"]["valueSecretRef"][
                "x-kubernetes-validations"
            ][0]["rule"],
            "self == oldSelf",
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
            by_id["keyVaultSecret"]["template"]["spec"]["forProvider"][
                "keyVaultId"
            ],
            "${keyVault.status.id}",
        )


if __name__ == "__main__":
    unittest.main()
