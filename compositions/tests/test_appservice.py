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
    def render_step(self, step, xr, observed):
        composition = yaml.safe_load((APP_SERVICE / "composition.yaml").read_text())
        result = subprocess.run(
            ["go", "run", str(RENDER)],
            input=json.dumps({
                "template": composition["spec"]["pipeline"][step]["input"]["inline"]["template"],
                "xr": xr,
                "observed": observed,
            }),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

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
        expected = list(
            yaml.safe_load_all(
                (FIXTURES / "expected-custom-domain.yaml").read_text()
            )
        )

        def render(observed):
            return self.render_step(0, xr, observed)

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

        for connection_details in ({}, {"attribute.custom_domain_verification_id": ""}):
            with self.subTest(connection_details=connection_details):
                missing_id = copy.deepcopy(observed)
                missing_id["linuxWebApp"]["connectionDetails"] = connection_details
                missing_id.pop("dnsTxtRecord")
                self.assertEqual(
                    [doc["kind"] for doc in render(missing_id)],
                    ["ServicePlan", "LinuxWebApp", "DNSCNAMERecord", "XAppService"],
                )

        before_txt = copy.deepcopy(observed)
        before_txt.pop("dnsTxtRecord")
        self.assertEqual(
            [doc["kind"] for doc in render(before_txt)],
            ["ServicePlan", "LinuxWebApp", "DNSCNAMERecord", "DNSTXTRecord", "XAppService"],
        )
        # A transient DNS readiness failure must not prune existing TLS resources.
        dns_pending = copy.deepcopy(observed)
        dns_pending["dnsTxtRecord"]["resource"]["status"] = {
            "conditions": [{"type": "Ready", "status": "False"}],
        }
        self.assertEqual(render(dns_pending), rendered)

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
            return self.render_step(1, xr, observed)

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

    def test_custom_domain_requires_every_child_synced_and_ready(self):
        xr = {
            "apiVersion": "azure.platform.example.org/v1alpha1",
            "kind": "XAppService",
            "metadata": {"name": "demo", "namespace": "demo"},
            "spec": {"customDomain": {"hostname": "portal.demo.example.org"}},
        }
        keys = (
            "servicePlan", "linuxWebApp", "dnsCnameRecord", "dnsTxtRecord",
            "customHostnameBinding", "managedCertificate", "certificateBinding",
        )
        observed = {
            key: {"resource": {"status": {"conditions": [
                {"type": "Synced", "status": "True"},
                {"type": "Ready", "status": "True"},
            ]}}}
            for key in keys
        }
        xr_doc, conditions = self.render_step(1, xr, observed)
        self.assertEqual(xr_doc["metadata"]["annotations"], {})
        self.assertEqual(conditions["conditions"][0]["status"], "True")
        self.assertIn("Synced=True and Ready=True", conditions["conditions"][0]["message"])

        for key in keys:
            with self.subTest(missing=key):
                missing = copy.deepcopy(observed)
                del missing[key]
                xr_doc, conditions = self.render_step(1, xr, missing)
                self.assertEqual(
                    xr_doc["metadata"]["annotations"]["gotemplating.fn.crossplane.io/ready"],
                    "False",
                )
                self.assertEqual(conditions["conditions"][0]["status"], "False")
                self.assertIn(key, conditions["conditions"][0]["message"])

            for condition_type in ("Synced", "Ready"):
                with self.subTest(resource=key, condition=condition_type):
                    pending = copy.deepcopy(observed)
                    for condition in pending[key]["resource"]["status"]["conditions"]:
                        if condition["type"] == condition_type:
                            condition["status"] = "False"
                    xr_doc, conditions = self.render_step(1, xr, pending)
                    self.assertEqual(
                        xr_doc["metadata"]["annotations"]["gotemplating.fn.crossplane.io/ready"],
                        "False",
                    )
                    self.assertEqual(conditions["conditions"][0]["status"], "False")
                    self.assertIn(key, conditions["conditions"][0]["message"])

        for conditions in ([], [{"type": "Synced", "status": "True"}],
                           [{"type": "Ready", "status": "True"},
                            {"type": "Synced", "status": "Unknown"}]):
            with self.subTest(incomplete_conditions=conditions):
                pending = copy.deepcopy(observed)
                pending["certificateBinding"]["resource"]["status"]["conditions"] = conditions
                _, result = self.render_step(1, xr, pending)
                self.assertEqual(result["conditions"][0]["status"], "False")
                self.assertIn("certificateBinding", result["conditions"][0]["message"])

    def test_empty_custom_domain_preserves_default_hostname_behavior(self):
        xr = copy.deepcopy(next(doc for doc in EXAMPLES if doc.get("kind") == "XAppService"))
        observed = {
            "servicePlan": {"resource": {"status": {
                "atProvider": {"id": "plan-id"},
                "conditions": [{"type": "Synced", "status": "True"}],
            }}},
            "linuxWebApp": {"resource": {"status": {
                "atProvider": {
                    "id": "app-id", "defaultHostname": "app.azurewebsites.net",
                },
                "conditions": [{"type": "Synced", "status": "True"}],
            }}},
        }
        for domain in (None, {}, {"hostname": "", "dnsZone": "", "dnsZoneResourceGroup": ""}):
            with self.subTest(domain=domain):
                if domain is None:
                    xr["spec"].pop("customDomain", None)
                else:
                    xr["spec"]["customDomain"] = domain
                rendered = self.render_step(0, xr, observed)
                self.assertEqual(
                    [doc["kind"] for doc in rendered],
                    ["ServicePlan", "LinuxWebApp", "XAppService"],
                )
                self.assertEqual(rendered[-1]["status"]["url"], "https://app.azurewebsites.net")
                self.assertNotIn("writeConnectionSecretToRef", rendered[1]["spec"])
                xr_doc, conditions = self.render_step(1, xr, observed)
                self.assertEqual(xr_doc["metadata"]["annotations"], {})
                self.assertEqual(conditions["conditions"][0]["status"], "True")


if __name__ == "__main__":
    unittest.main()
