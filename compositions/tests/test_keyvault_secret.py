import copy
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
        self.assertEqual(
            schema["properties"]["spec"]["properties"]["keyVaultRef"][
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
        self.assertEqual(
            [step["step"] for step in self.composition["spec"]["pipeline"]],
            ["kro", "check-key-vault-secret", "auto-ready"],
        )
        self.assertNotIn(
            "crossplane.io/external-name",
            by_id["keyVaultSecret"]["template"]["metadata"].get("annotations", {}),
        )

    def render_pipeline(self, observed, desired):
        desired = copy.deepcopy(desired)
        for resource in desired.values():
            resource["resource"].setdefault("apiVersion", "keyvault.azure.m.upbound.io/v1beta1")
            resource["resource"].setdefault("kind", "Secret")
            resource["resource"].setdefault("metadata", {})
            resource["resource"]["spec"]["forProvider"].setdefault("name", "password")
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

    def render_readiness(self, observed, desired):
        return [
            resource for resource in self.render_pipeline(observed, desired)
            if resource["kind"] in ("XKeyVaultSecret", "ClaimConditions")
        ]

    def render_identity(self, external_name="", secret_id=""):
        observed = {"keyVaultSecret": {"resource": {
            "metadata": {"annotations": {
                "crossplane.io/external-name": external_name,
            }},
            "status": {"atProvider": {"id": secret_id}},
        }}}
        desired = {"keyVaultSecret": {"resource": {
            "spec": {"forProvider": {
                "keyVaultId": "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.KeyVault/vaults/demo-vault",
                "name": "password",
                "valueSecretRef": {"name": "existing-credentials", "key": "password"},
            }},
        }}}
        return self.render_pipeline(observed, desired)[0]

    def test_new_secret_does_not_force_a_bare_external_name(self):
        resource = self.render_identity()
        self.assertNotIn(
            "crossplane.io/external-name", resource["metadata"]["annotations"],
        )
        self.assertEqual(
            resource["spec"]["forProvider"]["valueSecretRef"],
            {"name": "existing-credentials", "key": "password"},
        )

    def test_reconciles_preserve_provider_owned_version_instead_of_stale_status(self):
        external_name = "password/new-version"
        for _ in range(2):
            resource = self.render_identity(
                external_name,
                "https://demo-vault.vault.azure.net/secrets/password/old-version",
            )
            external_name = resource["metadata"]["annotations"][
                "crossplane.io/external-name"
            ]
            self.assertEqual(external_name, "password/new-version")
            self.assertEqual(
                resource["metadata"]["annotations"][
                    "gotemplating.fn.crossplane.io/composition-resource-name"
                ],
                "keyVaultSecret",
            )

    def test_recovers_lost_identity_from_matching_versioned_status_id(self):
        for annotation in ("", "password"):
            with self.subTest(annotation=annotation):
                resource = self.render_identity(
                    annotation,
                    "https://demo-vault.vault.azure.net/secrets/password/version-1",
                )
                self.assertEqual(
                    resource["metadata"]["annotations"]["crossplane.io/external-name"],
                    "password/version-1",
                )

    def test_does_not_adopt_another_vault_secret_or_versionless_id(self):
        for secret_id in (
            "https://other-vault.vault.azure.net/secrets/password/version-1",
            "https://demo-vault.vault.azure.net/secrets/another-secret/version-1",
            "https://demo-vault.vault.azure.net/secrets/password",
            "https://demo-vault.vault.azure.net/secrets/password/",
            "https://demo-vault.vault.azure.net/secrets/password/version/extra",
        ):
            with self.subTest(secret_id=secret_id):
                resource = self.render_identity("password", secret_id)
                self.assertNotIn(
                    "crossplane.io/external-name", resource["metadata"]["annotations"],
                )

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
