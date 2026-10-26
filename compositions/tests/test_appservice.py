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
            }
        )
        composition = yaml.safe_load((APP_SERVICE / "composition.yaml").read_text())
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


if __name__ == "__main__":
    unittest.main()
