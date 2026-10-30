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
            spec["properties"]["vaultName"]["x-kubernetes-validations"][0]["rule"],
            "self == oldSelf",
        )
        self.assertEqual(
            spec["properties"]["name"]["x-kubernetes-validations"][0]["rule"],
            "self == oldSelf",
        )
        self.assertIn(
            "metadata.name",
            spec["properties"]["appServiceRef"]["properties"]["name"][
                "description"
            ],
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
        self.assertNotIn("schema", str(graph["status"]))
        self.assertIn(
            'keyVault.metadata.annotations["crossplane.io/external-name"]',
            graph["status"]["vaultName"],
        )
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

    def test_replacement_deployment_matches_team_configuration(self):
        teams = ROOT.parent / "teams"
        advisor = yaml.safe_load(
            (teams / "app-hosting-advisor/kustomization.yaml").read_text()
        )
        self.assertIn("infrastructure.yaml", advisor["resources"])
        self.assertIn("keyvault-replacement.yaml", advisor["resources"])

        # Demo workloads remain in Git but are disabled to reduce running costs.
        demo = yaml.safe_load(
            (teams / "crd-dev/kustomization.yaml").read_text()
        )
        self.assertEqual(demo["resources"], ["../_base"])

    def test_replacement_manifests_reuse_existing_credentials(self):
        teams = ROOT.parent / "teams"
        expected = {
            "app-hosting-advisor": {
                "appadvisor-github-client-id-next": (
                    "appadvisor-next", "appadvisor-github-oauth", "client-id",
                ),
                "appadvisor-github-client-secret-next": (
                    "appadvisor-next", "appadvisor-github-oauth", "client-secret",
                ),
            },
            "crd-dev": {
                "keyvault-demo-value-next": (
                    "crd-dev-keyvault-next", "keyvault-demo-value", "password",
                ),
                "webdemo01-database-password-next": (
                    "webdemo01-keyvault-next", "webdemo01-database-admin", "password",
                ),
            },
        }
        vault_names = set()
        for team, secrets in expected.items():
            folder = teams / team
            kustomization = yaml.safe_load(
                (folder / "kustomization.yaml").read_text()
            )
            documents = list(yaml.safe_load_all(
                (folder / "keyvault-replacement.yaml").read_text()
            ))
            vaults = {
                doc["metadata"]["name"]: doc for doc in documents
                if doc["kind"] == "XKeyVault"
            }
            copies = {
                doc["metadata"]["name"]: doc for doc in documents
                if doc["kind"] == "XKeyVaultSecret"
            }
            self.assertEqual(set(copies), set(secrets))
            self.assertEqual(len(documents), len(vaults) + len(copies))
            for name, (vault, source, key) in secrets.items():
                self.assertEqual(copies[name]["spec"]["keyVaultRef"]["name"], vault)
                self.assertEqual(
                    copies[name]["spec"]["valueSecretRef"],
                    {"name": source, "key": key},
                )
                self.assertIn(vault, vaults)
            for doc in documents:
                self.assertEqual(
                    doc["metadata"].get("annotations", {}).get(
                        "kustomize.toolkit.fluxcd.io/prune"
                    ),
                    "disabled" if team == "app-hosting-advisor" else None,
                )
            for vault in vaults.values():
                name = vault["spec"]["vaultName"]
                self.assertNotIn(name, vault_names)
                vault_names.add(name)
                self.assertLessEqual(len(name), 24)
                self.assertRegex(name, "^[a-zA-Z][a-zA-Z0-9-]*[a-zA-Z0-9]$")
            if team == "app-hosting-advisor":
                self.assertIn("infrastructure.yaml", kustomization["resources"])
                self.assertEqual(
                    vaults["appadvisor-next"]["spec"]["appServiceRef"]["name"],
                    "appadvisor",
                )
                original = list(yaml.safe_load_all(
                    (folder / "infrastructure.yaml").read_text()
                ))
                original_vault = next(d for d in original if d["kind"] == "XKeyVault")
                app = next(d for d in original if d["kind"] == "XAppService")
                self.assertEqual(
                    original_vault["spec"]["vaultName"], "kv-appadvisor-lsdev",
                )
                for setting in app["spec"]["keyVaultSecretEnvVars"].values():
                    self.assertEqual(setting["vaultName"], "kv-appadvisor-lsdev")
            else:
                self.assertEqual(
                    vaults["webdemo01-keyvault-next"]["spec"]["appServiceRef"]["name"],
                    "webdemo01",
                )
        self.assertEqual(len(vault_names), 3)


if __name__ == "__main__":
    unittest.main()
