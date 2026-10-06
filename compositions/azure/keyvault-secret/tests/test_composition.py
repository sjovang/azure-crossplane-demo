import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class KeyVaultSecretCompositionTests(unittest.TestCase):
    def setUp(self):
        self.xrd = yaml.safe_load((ROOT / "xrd.yaml").read_text())
        self.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())
        self.expected = yaml.safe_load((ROOT / "tests/expected.yaml").read_text())

    def test_public_api_contract(self):
        version = self.xrd["spec"]["versions"][0]
        schema = version["schema"]["openAPIV3Schema"]

        self.assertEqual(self.xrd["spec"]["scope"], "Namespaced")
        self.assertEqual(
            schema["properties"]["spec"]["required"],
            ["name", "keyVaultRef", "valueSecretRef"],
        )
        self.assertIn("id", schema["properties"]["status"]["properties"])

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
