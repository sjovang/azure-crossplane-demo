import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT.parents[1] / "azure/tests/render.go"


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
        self.assertEqual(
            by_id["appService"]["template"]["spec"]["keyVaultSecretEnvVars"],
            self.expected["keyVaultSecretAppSettings"],
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
            by_id["keyVaultSecret"]["template"]["spec"]["valueSecretRef"]["name"],
            "${databaseCredentials.metadata.name}",
        )
        self.assertEqual(
            by_id["passwordExternalSecret"]["template"]["spec"]["target"][
                "creationPolicy"
            ],
            "Merge",
        )
        self.assertEqual(
            by_id["databaseCredentials"]["readyWhen"],
            ["${databaseCredentials.?data.?password.hasValue()}"],
        )
        self.assertEqual(
            by_id["entraClientSecret"]["template"]["spec"]["valueSecretRef"],
            {
                "name": "${enterpriseApp.status.connectionSecretName}",
                "key": "value",
            },
        )
        self.assertNotIn("enterpriseAppConnectionSecret", by_id)
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
            by_id["keyVault"]["template"]["kind"],
            "XKeyVault",
        )
        self.assertEqual(
            by_id["keyVault"]["template"]["spec"]["appServiceRef"]["name"],
            "${schema.spec.name}",
        )

    def test_example_supplies_required_fields(self):
        required = self.xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["required"]
        self.assertTrue(all(field in self.xr["spec"] for field in required))
        self.assertTrue(all(field in self.auth_xr["spec"] for field in required))
        self.assertTrue(self.auth_xr["spec"]["entraIdAuth"]["enabled"])

    def test_unconditional_resources_do_not_depend_on_optional_auth(self):
        resources = self.composition["spec"]["pipeline"][0]["input"]["resources"]
        optional = {r["id"] for r in resources if "includeWhen" in r}
        for resource in resources:
            if resource["id"] in optional:
                continue
            references = set(re.findall(r"\b(\w+)\.", json.dumps(resource)))
            self.assertFalse(optional & references, resource["id"])

    def render_configuration(self, auth_enabled=False, observed=None, desired=None):
        xr = copy.deepcopy(self.auth_xr if auth_enabled else self.xr)
        template = self.composition["spec"]["pipeline"][1]["input"]["inline"][
            "template"
        ]
        result = subprocess.run(
            ["go", "run", str(RENDER)],
            input=json.dumps(
                {
                    "template": template,
                    "xr": xr,
                    "observed": observed or {},
                    "desired": desired or {},
                }
            ),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def ready_resources(self, auth_enabled=False):
        names = [
            "resourceGroup", "database", "passwordExternalSecret", "keyVault",
            "keyVaultSecret", "appService",
        ]
        if auth_enabled:
            names += ["enterpriseApp", "entraClientSecret"]
        resources = {
            name: {"resource": {"status": {"conditions": [
                {"type": "Ready", "status": "True"}
            ]}}}
            for name in names
        }
        resources["keyVault"]["resource"]["spec"] = {
            "vaultName": "kv-webdemo01-example"
        }
        resources["keyVault"]["resource"]["status"][
            "tenantId"
        ] = "tenant-id"
        if auth_enabled:
            resources["enterpriseApp"]["resource"]["status"]["clientId"] = "client-id"
            resources["entraClientSecret"]["resource"]["spec"] = {
                "name": "entra-client-secret"
            }
        return resources

    def desired_app(self):
        graph = self.composition["spec"]["pipeline"][0]["input"]
        app = copy.deepcopy(next(
            r["template"] for r in graph["resources"] if r["id"] == "appService"
        ))
        return {"appService": {"resource": app}}

    def assert_composite_ready(self, xr, ready):
        self.assertEqual(
            xr["metadata"]["annotations"]["gotemplating.fn.crossplane.io/ready"],
            str(ready),
        )

    def test_auth_disabled_preserves_base_app_and_is_ready(self):
        desired = self.desired_app()
        rendered = self.render_configuration(
            observed=self.ready_resources(), desired=desired
        )
        app, xr, condition = rendered
        self.assertEqual(
            app["spec"]["image"],
            desired["appService"]["resource"]["spec"]["image"],
        )
        for key in (
            "AUTH_MICROSOFT_CLIENT_ID", "AUTH_MICROSOFT_CLIENT_SECRET",
            "AUTH_MICROSOFT_TENANT_ID",
        ):
            self.assertEqual(app["spec"]["envVars"][key], "")
        self.assert_composite_ready(xr, True)
        self.assertEqual(condition["conditions"][0]["status"], "True")

    def test_auth_enabled_uses_key_vault_secret_reference(self):
        app, xr, _ = self.render_configuration(
            auth_enabled=True, observed=self.ready_resources(True),
            desired=self.desired_app(),
        )
        settings = app["spec"]["envVars"]
        self.assertEqual(settings["AUTH_MICROSOFT_CLIENT_ID"], "client-id")
        self.assertEqual(settings["AUTH_MICROSOFT_TENANT_ID"], "tenant-id")
        self.assertEqual(settings["AUTH_MICROSOFT_CLIENT_SECRET"], "")
        self.assertEqual(
            app["spec"]["keyVaultSecretEnvVars"][
                "AUTH_MICROSOFT_CLIENT_SECRET"
            ],
            {
                "vaultName": "kv-webdemo01-example",
                "secretName": "entra-client-secret",
            },
        )
        self.assert_composite_ready(xr, True)

    def test_database_reference_uses_key_vault_contract(self):
        self.assertEqual(
            self.desired_app()["appService"]["resource"]["spec"][
                "keyVaultSecretEnvVars"
            ][
                "DATABASE_PASSWORD"
            ],
            {
                "vaultName": "${keyVault.spec.vaultName}",
                "secretName": "${keyVaultSecret.spec.name}",
            },
        )

    def test_missing_or_unready_required_resource_blocks_environment(self):
        for missing in ("keyVault", "appService", "keyVaultSecret"):
            with self.subTest(missing=missing):
                observed = self.ready_resources()
                del observed[missing]
                xr, condition = self.render_configuration(observed=observed)
                self.assert_composite_ready(xr, False)
                self.assertIn(missing, condition["conditions"][0]["message"])
        observed = self.ready_resources()
        observed["keyVaultSecret"]["resource"]["status"]["conditions"][0][
            "status"
        ] = "False"
        xr, _ = self.render_configuration(observed=observed)
        self.assert_composite_ready(xr, False)

    def test_auth_enabled_waits_for_optional_resources_and_outputs(self):
        observed = self.ready_resources(True)
        del observed["entraClientSecret"]
        _, xr, condition = self.render_configuration(
            auth_enabled=True, observed=observed, desired=self.desired_app(),
        )
        self.assert_composite_ready(xr, False)
        self.assertIn("entraClientSecret", condition["conditions"][0]["message"])
        observed = self.ready_resources(True)
        del observed["entraClientSecret"]["resource"]["spec"]
        _, xr, condition = self.render_configuration(
            auth_enabled=True, observed=observed, desired=self.desired_app(),
        )
        self.assert_composite_ready(xr, False)
        self.assertIn("authSettings", condition["conditions"][0]["message"])
        observed = self.ready_resources(True)
        del observed["keyVault"]
        _, xr, condition = self.render_configuration(
            auth_enabled=True, observed=observed, desired=self.desired_app(),
        )
        self.assert_composite_ready(xr, False)
        self.assertIn("keyVault", condition["conditions"][0]["message"])

    def test_observed_app_without_desired_state_is_not_ready(self):
        xr, _ = self.render_configuration(observed=self.ready_resources())
        self.assert_composite_ready(xr, False)

    def test_no_resources_is_not_ready(self):
        xr, condition = self.render_configuration()
        self.assert_composite_ready(xr, False)
        self.assertIn("appService", condition["conditions"][0]["message"])


if __name__ == "__main__":
    unittest.main()
