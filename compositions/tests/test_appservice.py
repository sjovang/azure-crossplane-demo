import base64
import copy
import json
import subprocess
import unittest
from pathlib import Path

import yaml


TESTS = Path(__file__).resolve().parent
APIS = TESTS.parent / "apis"
REPO = TESTS.parents[1]
RENDER = TESTS / "render.go"
APP_SERVICE = APIS / "azure/appservice"
FIXTURES = TESTS / "azure/appservice"
EXAMPLES = list(yaml.safe_load_all((REPO / "teams/crd-dev/apps.yaml").read_text()))


class AppServiceCompositionTests(unittest.TestCase):
    def test_custom_domain(self):
        xr = copy.deepcopy(next(doc for doc in EXAMPLES if doc.get("kind") == "XAppService"))
        xr["spec"].update(
            {
                "location": "swedencentral",
                "size": "small",
                "port": 80,
                "customDomain": {
                    "hostname": "portal.demo.example.org",
                    "dnsZone": "demo.example.org",
                    "dnsZoneResourceGroup": "rg-dns",
                },
                "keyVaultSecretEnvVars": {
                    "CLIENT_SECRET": {
                        "vaultName": "kv-appdemo",
                        "secretName": "client-secret",
                    }
                },
            }
        )
        composition = yaml.safe_load((APP_SERVICE / "composition.yaml").read_text())
        self.assertEqual(
            [step["step"] for step in composition["spec"]["pipeline"]],
            ["render-appservice", "check-appservice-sync", "auto-ready"],
        )
        template = composition["spec"]["pipeline"][0]["input"]["inline"]["template"]
        expected = list(
            yaml.safe_load_all(
                (FIXTURES / "expected-custom-domain.yaml").read_text()
            )
        )

        def render(observed):
            result = subprocess.run(
                ["go", "run", str(RENDER)],
                input=json.dumps({"template": template, "xr": xr, "observed": observed}),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

        # Before the web app exists only the CNAME can be rendered: the TXT
        # value and the bindings depend on observed state.
        kinds = [doc["kind"] for doc in expected]
        initial = [
            doc
            for doc in expected
            if doc["kind"] in ("ServicePlan", "LinuxWebApp", "DNSCNAMERecord")
        ]
        self.assertEqual(render({}), initial)

        verification_id = base64.b64encode(b"ABC123").decode()
        observed = {
            "servicePlan": {"resource": {"status": {"atProvider": {"id": "plan-id"}}}},
            "linuxWebApp": {
                "resource": {
                    "status": {
                        "atProvider": {
                            "id": "app-id",
                            "defaultHostname": "app.azurewebsites.net",
                        }
                    }
                },
                "connectionDetails": {
                    "attribute.custom_domain_verification_id": verification_id
                },
            },
            "dnsCnameRecord": {"resource": {"kind": "DNSCNAMERecord"}},
            "dnsTxtRecord": {"resource": {"kind": "DNSTXTRecord"}},
        }
        rendered = render(observed)
        self.assertEqual([doc["kind"] for doc in rendered], kinds + ["XAppService"])
        self.assertEqual(rendered[:-1], expected)
        self.assertEqual(rendered[-1]["status"]["url"], "https://portal.demo.example.org")

        xr["spec"]["keyVaultSecretEnvVars"]["CLIENT_SECRET"][
            "secretVersion"
        ] = "version-123"
        versioned = render(observed)
        linux_web_app = next(doc for doc in versioned if doc["kind"] == "LinuxWebApp")
        self.assertEqual(
            linux_web_app["spec"]["forProvider"]["appSettings"]["CLIENT_SECRET"],
            "@Microsoft.KeyVault(SecretUri=https://kv-appdemo.vault.azure.net/"
            "secrets/client-secret/version-123)",
        )

    def test_synced_children_gate_xappservice_readiness(self):
        composition = yaml.safe_load((APP_SERVICE / "composition.yaml").read_text())
        template = composition["spec"]["pipeline"][1]["input"]["inline"]["template"]
        xr = {
            "apiVersion": "azure.platform.example.org/v1alpha1",
            "kind": "XAppService",
            "metadata": {"name": "demo", "namespace": "demo"},
            "spec": {},
        }

        def render(service_plan_synced, linux_web_app_synced):
            observed = {
                "servicePlan": {
                    "resource": {
                        "status": {
                            "conditions": [{
                                "type": "Synced",
                                "status": service_plan_synced,
                            }, {
                                "type": "Ready",
                                "status": "True",
                            }],
                        },
                    },
                },
                "linuxWebApp": {
                    "resource": {
                        "status": {
                            "conditions": [{
                                "type": "Synced",
                                "status": linux_web_app_synced,
                            }, {
                                "type": "Ready",
                                "status": "True",
                            }],
                        },
                    },
                },
            }
            result = subprocess.run(
                ["go", "run", str(RENDER)],
                input=json.dumps({
                    "template": template,
                    "xr": xr,
                    "observed": observed,
                }),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return list(yaml.safe_load_all(result.stdout))

        for service_plan_synced, linux_web_app_synced, pending_resource in (
            ("False", "True", "servicePlan"),
            ("True", "False", "linuxWebApp"),
            (None, "True", "servicePlan"),
        ):
            with self.subTest(
                service_plan_synced=service_plan_synced,
                linux_web_app_synced=linux_web_app_synced,
            ):
                xr_doc, claim_conditions = render(
                    service_plan_synced,
                    linux_web_app_synced,
                )
                self.assertEqual(
                    xr_doc["metadata"]["annotations"][
                        "gotemplating.fn.crossplane.io/ready"
                    ],
                    "False",
                )
                self.assertEqual(
                    claim_conditions["conditions"][0]["type"],
                    "AppServiceSynced",
                )
                self.assertEqual(
                    claim_conditions["conditions"][0]["status"],
                    "False",
                )
                self.assertIn(
                    pending_resource,
                    claim_conditions["conditions"][0]["message"],
                )

        xr_doc, claim_conditions = render("True", "True")
        self.assertEqual(xr_doc["metadata"]["annotations"], {})
        self.assertEqual(claim_conditions["conditions"][0]["status"], "True")


if __name__ == "__main__":
    unittest.main()
