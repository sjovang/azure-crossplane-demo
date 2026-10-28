import json
import subprocess
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
        self.readiness_template = self.composition["spec"]["pipeline"][1]["input"][
            "inline"
        ]["template"]

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
        self.assertIn(
            "metadata.name",
            schema["properties"]["spec"]["properties"]["keyVaultRef"][
                "properties"
            ]["name"]["description"],
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
        self.assertEqual(
            [step["step"] for step in self.composition["spec"]["pipeline"]],
            ["kro", "check-key-vault-secret", "auto-ready"],
        )

    def render_readiness(self, observed, desired):
        result = subprocess.run(
            ["go", "run", str(ROOT / "tests/render.go")],
            input=json.dumps({
                "template": self.readiness_template,
                "xr": {
                    "apiVersion": "azure.platform.example.org/v1alpha1",
                    "kind": "XKeyVaultSecret",
                    "metadata": {"name": "secret-xr", "namespace": "demo"},
                    "spec": {"keyVaultRef": {"name": "vault-with-suffix"}},
                },
                "observed": observed,
                "desired": desired,
            }),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(yaml.safe_load_all(result.stdout))

    def test_missing_dependency_or_secret_output_blocks_readiness(self):
        ready_secret = {
            "resource": {
                "status": {
                    "atProvider": {
                        "id": "/vaults/demo/secrets/password/version-1",
                        "versionlessId": "/vaults/demo/secrets/password",
                    },
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
        }
        cases = {
            "missing desired secret": (
                {"keyVaultSecret": ready_secret},
                {},
            ),
            "unresolved Key Vault": (
                {"keyVaultSecret": ready_secret},
                {"keyVaultSecret": {"resource": {"spec": {"forProvider": {}}}}},
            ),
            "missing observed secret": (
                {},
                {"keyVaultSecret": {"resource": {"spec": {"forProvider": {
                    "keyVaultId": "/vaults/demo",
                }}}}},
            ),
            "missing Azure ID": (
                {"keyVaultSecret": {"resource": {
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }}},
                {"keyVaultSecret": {"resource": {"spec": {"forProvider": {
                    "keyVaultId": "/vaults/demo",
                }}}}},
            ),
            "managed secret not ready": (
                {"keyVaultSecret": {"resource": {
                    "status": {
                        "atProvider": {"id": "/vaults/demo/secrets/password/version-1"},
                        "conditions": [{"type": "Ready", "status": "False"}],
                    },
                }}},
                {"keyVaultSecret": {"resource": {"spec": {"forProvider": {
                    "keyVaultId": "/vaults/demo",
                }}}}},
            ),
        }

        for case, (observed, desired) in cases.items():
            with self.subTest(case=case):
                composite, condition = self.render_readiness(observed, desired)
                self.assertEqual(
                    composite["metadata"]["annotations"][
                        "gotemplating.fn.crossplane.io/ready"
                    ],
                    "False",
                )
                self.assertEqual(condition["conditions"][0]["status"], "False")

    def test_ready_secret_outputs_versioned_and_versionless_ids(self):
        secret_id = "/vaults/demo/secrets/password/version-1"
        versionless_id = "/vaults/demo/secrets/password"
        composite, condition = self.render_readiness(
            {"keyVaultSecret": {"resource": {"status": {
                "atProvider": {
                    "id": secret_id,
                    "versionlessId": versionless_id,
                },
                "conditions": [{"type": "Ready", "status": "True"}],
            }}}},
            {"keyVaultSecret": {"resource": {"spec": {"forProvider": {
                "keyVaultId": "/vaults/demo",
            }}}}},
        )
        self.assertEqual(composite["metadata"]["annotations"], {})
        self.assertEqual(composite["status"]["id"], secret_id)
        self.assertEqual(composite["status"]["versionlessId"], versionless_id)
        self.assertEqual(condition["conditions"][0]["status"], "True")


if __name__ == "__main__":
    unittest.main()
