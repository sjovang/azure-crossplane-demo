"""Opt-in pinned-function tests using synthetic observations; no cluster access."""

import base64
import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apis/azure/appservice"
RESOURCE_KEY = "crossplane.io/composition-resource-name"


@unittest.skipUnless(
    os.environ.get("RUN_APPSERVICE_RENDER") == "1",
    "Set RUN_APPSERVICE_RENDER=1; requires Docker and crossplane.",
)
class AppServiceRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = tempfile.TemporaryDirectory(prefix=".appservice-render-", dir=ROOT / "tests")
        cls.addClassCleanup(work.cleanup)
        cls.work = Path(work.name)
        cls.write("functions.yaml", [
            yaml.safe_load((
                ROOT.parent / f"clusters/base/crossplane/functions/{name}.yaml"
            ).read_text())
            for name in ("go-templating", "auto-ready")
        ])

    @classmethod
    def write(cls, name, documents):
        path = cls.work / name
        path.write_text(yaml.safe_dump_all(documents, explicit_start=True))
        return path

    def render(self, xr, observed=(), verification_id=None):
        composition = yaml.safe_load((API / "composition.yaml").read_text())
        if verification_id is not None:
            # The CLI cannot supply observed connectionDetails. Seed the real
            # function's request-shaped map with synthetic public DNS material.
            encoded = base64.b64encode(verification_id.encode()).decode()
            template = composition["spec"]["pipeline"][0]["input"]["inline"]
            template["template"] = (
                '{{- $_ := set (get .observed.resources "linuxWebApp") "connectionDetails" '
                f'(dict "attribute.custom_domain_verification_id" "{encoded}") -}}}}\n'
                + template["template"]
            )
        result = subprocess.run([
            "crossplane", "render", str(self.write("xr.yaml", [xr])),
            str(self.write("composition.yaml", [composition])),
            str(self.work / "functions.yaml"),
            "--observed-resources", str(self.write("observed.yaml", observed)),
            "--crossplane-version", "v2.4.0", "--timeout", "2m",
        ], capture_output=True, text=True, cwd=ROOT.parent, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(yaml.safe_load_all(result.stdout))

    def resources(self, rendered):
        return {
            doc["metadata"]["annotations"][RESOURCE_KEY]: doc
            for doc in rendered if doc["kind"] != "XAppService"
        }

    def mark_ready(self, resources):
        for key, resource in resources.items():
            resource["status"] = {
                "atProvider": {"id": f"/diagnostic/{key}"},
                "conditions": [
                    {"type": "Synced", "status": "True"},
                    {"type": "Ready", "status": "True"},
                ],
            }
            if key == "linuxWebApp":
                resource["status"]["atProvider"]["defaultHostname"] = "app-demo.azurewebsites.net"

    def assert_ready(self, rendered, status):
        conditions = {
            item["type"]: item["status"]
            for item in rendered[0]["status"]["conditions"]
        }
        self.assertEqual(conditions["Ready"], status)
        self.assertEqual(conditions["AppServiceSynced"], status)

    def test_custom_domain_lifecycle_preserves_resource_identities(self):
        xr = {
            "apiVersion": "azure.platform.example.org/v1alpha1",
            "kind": "XAppService",
            "metadata": {"name": "demo", "namespace": "demo"},
            "spec": {
                "name": "demo", "location": "swedencentral", "size": "small",
                "image": "nginx:latest", "port": 80,
                "resourceGroupRef": {"name": "demo"},
                "customDomain": {
                    "hostname": "portal.demo.example.org", "dnsZone": "demo.example.org",
                    "dnsZoneResourceGroup": "rg-dns",
                },
            },
        }
        initial = self.render(xr)
        self.assert_ready(initial, "False")
        base = self.resources(initial)
        self.assertEqual(set(base), {"servicePlan", "linuxWebApp", "dnsCnameRecord"})
        self.assertEqual(base["dnsCnameRecord"]["spec"]["forProvider"]["record"],
                         "app-demo.azurewebsites.net")
        names = {key: doc["metadata"]["name"] for key, doc in base.items()}
        self.mark_ready(base)
        for verification_id in (None, ""):
            with self.subTest(verification_id=verification_id):
                missing_id = self.render(xr, list(base.values()), verification_id)
                self.assert_ready(missing_id, "False")
                self.assertNotIn("dnsTxtRecord", self.resources(missing_id))

        txt_pending = self.render(xr, list(base.values()), "ABC123")
        self.assert_ready(txt_pending, "False")
        with_txt = self.resources(txt_pending)
        self.assertEqual(set(with_txt), set(base) | {"dnsTxtRecord"})
        self.assertEqual(with_txt["dnsTxtRecord"]["spec"]["forProvider"]["record"],
                         [{"value": "ABC123"}])
        self.assertEqual(with_txt["dnsTxtRecord"]["metadata"]["annotations"]["crossplane.io/external-name"],
                         "asuid.portal")
        self.mark_ready(with_txt)
        tls_pending = self.render(xr, list(with_txt.values()), "ABC123")
        self.assert_ready(tls_pending, "False")
        complete = self.resources(tls_pending)
        self.assertEqual(set(complete), set(with_txt) | {
            "customHostnameBinding", "managedCertificate", "certificateBinding",
        })
        self.assertEqual(complete["certificateBinding"]["spec"]["forProvider"]["sslState"],
                         "SniEnabled")
        self.assertEqual(complete["customHostnameBinding"]["metadata"]["annotations"]["crossplane.io/external-name"],
                         "portal.demo.example.org")
        self.mark_ready(complete)
        ready = self.render(xr, list(complete.values()), "ABC123")
        self.assert_ready(ready, "True")
        self.assertEqual(ready[0]["status"]["url"], "https://portal.demo.example.org")
        for key, name in names.items():
            self.assertEqual(self.resources(ready)[key]["metadata"]["name"], name)

        for key, condition_type in (
            ("dnsTxtRecord", "Ready"),
            ("certificateBinding", "Ready"),
            ("certificateBinding", "Synced"),
        ):
            with self.subTest(resource=key, condition=condition_type):
                pending = copy.deepcopy(complete)
                for condition in pending[key]["status"]["conditions"]:
                    if condition["type"] == condition_type:
                        condition["status"] = "False"
                rendered = self.render(xr, list(pending.values()), "ABC123")
                self.assert_ready(rendered, "False")
                self.assertEqual(set(self.resources(rendered)), set(complete))

        xr["spec"].pop("customDomain")
        default_domain = self.render(xr, list(base.values()))
        self.assert_ready(default_domain, "True")
        self.assertEqual(default_domain[0]["status"]["url"], "https://app-demo.azurewebsites.net")


if __name__ == "__main__":
    unittest.main()
