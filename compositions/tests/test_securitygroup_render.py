"""Opt-in rendering tests against the pinned kro and go-templating functions."""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apis/entraid/securitygroup"
GROUP_API = "groups.azuread.m.upbound.io/v1beta1"


@unittest.skipUnless(
    os.environ.get("RUN_SECURITYGROUP_RENDER") == "1",
    "Set RUN_SECURITYGROUP_RENDER=1; requires Docker, crossplane and gh.",
)
class SecurityGroupRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "tests/.securitygroup-render"
        cls.work.mkdir()
        cls.addClassCleanup(shutil.rmtree, cls.work)
        provider = yaml.safe_load((
            ROOT.parent / "clusters/base/crossplane/providers/azure/provider-azuread.yaml"
        ).read_text())
        version = provider["spec"]["package"].rsplit(":", 1)[1]
        raw = subprocess.check_output([
            "gh", "api",
            "repos/upbound/provider-azuread/contents/package/crds/"
            f"groups.azuread.m.upbound.io_groups.yaml?ref={version}",
            "-H", "Accept: application/vnd.github.raw+json",
        ], text=True)
        crd = yaml.safe_load(raw)
        group_schema = next(
            item["schema"]["openAPIV3Schema"]
            for item in crd["spec"]["versions"] if item["name"] == "v1beta1"
        )
        xrd = yaml.safe_load((API / "definition.yaml").read_text())
        definitions = [
            (
                "XSecurityGroup",
                "entraid.platform.example.org/v1alpha1",
                xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"],
            ),
            ("Group", GROUP_API, group_schema),
        ]
        components = {}
        for kind, api_version, schema in definitions:
            group, api_version = api_version.split("/")
            schema["properties"].update({
                "apiVersion": {"type": "string"},
                "kind": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        field: {"type": "string"}
                        for field in ("name", "namespace", "uid")
                    },
                },
            })
            schema["x-kubernetes-group-version-kind"] = [{
                "group": group, "version": api_version, "kind": kind,
            }]
            components[kind] = schema
        schemas = cls.work / "schemas"
        schemas.mkdir()
        (schemas / "schemas.json").write_text(json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Security group test schemas", "version": "1"},
            "paths": {},
            "components": {"schemas": components},
        }))
        functions = [
            yaml.safe_load((
                ROOT.parent / f"clusters/base/crossplane/functions/{name}.yaml"
            ).read_text())
            for name in ("kro", "go-templating", "auto-ready")
        ]
        cls.write("functions.yaml", functions)

    @classmethod
    def write(cls, name, documents):
        path = cls.work / name
        path.write_text(yaml.safe_dump_all(documents, explicit_start=True))
        return path

    def test_external_membership_omits_provider_field_without_clearing_it(self):
        composition = yaml.safe_load((API / "composition.yaml").read_text())
        composition["spec"]["pipeline"] = [
            step for step in composition["spec"]["pipeline"]
            if step["functionRef"]["name"] != "function-msgraph"
        ]
        composition_path = self.write("composition.yaml", [composition])
        xr = {
            "apiVersion": "entraid.platform.example.org/v1alpha1",
            "kind": "XSecurityGroup",
            "metadata": {
                "name": "engineering", "namespace": "developer-portal",
                "uid": "11111111-1111-4111-8111-111111111111",
            },
            "spec": {
                "name": "Engineering", "membershipMode": "Declared",
                "members": [], "owners": [],
            },
        }

        def render(observed=()):
            result = subprocess.run([
                "crossplane", "render", str(self.write("xr.yaml", [xr])),
                str(composition_path), str(self.work / "functions.yaml"),
                "--observed-resources", str(self.write("observed.yaml", observed)),
                "--required-resources", str(self.write("required.yaml", [xr])),
                "--required-schemas", str(self.work / "schemas"),
                "--crossplane-version", "v2.4.0",
                "--cache-dir", str(self.work / "cache"), "--timeout", "2m",
            ], capture_output=True, text=True, cwd=ROOT.parent, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            resources = list(yaml.safe_load_all(result.stdout))
            groups = [item for item in resources if item["kind"] == "Group"]
            self.assertEqual(len(groups), 1, resources)
            return groups[0]

        xr["spec"]["members"] = ["ALICE@example.org", "missing@example.org"]
        xr["spec"]["owners"] = ["owner@example.org"]
        xr["status"] = {
            "resolvedMembers": [
                {"userPrincipalName": "alice@example.org", "id": "member-id"},
                {"userPrincipalName": "removed@example.org", "id": "removed-id"},
            ],
            "resolvedOwners": [
                {"userPrincipalName": "OWNER@example.org", "id": "owner-id"},
            ],
        }
        declared = render()
        self.assertEqual(declared["spec"]["forProvider"]["members"], ["member-id"])
        self.assertEqual(declared["spec"]["forProvider"]["owners"], ["owner-id"])
        self.assertNotIn("managementPolicies", declared["spec"])

        xr["spec"]["membershipMode"] = "External"
        for observed in ([], [declared]):
            with self.subTest(already_observed=bool(observed)):
                external = render(observed)
                self.assertNotIn("members", external["spec"]["forProvider"])
                self.assertNotIn("initProvider", external["spec"])
                self.assertEqual(external["spec"]["managementPolicies"], [
                    "Observe", "Create", "Update", "Delete",
                ])
                self.assertEqual(external["spec"]["forProvider"]["owners"], ["owner-id"])

        xr["spec"]["membershipMode"] = "Declared"
        returned = render([external])
        self.assertEqual(returned["spec"]["forProvider"]["members"], ["member-id"])
        self.assertNotIn("managementPolicies", returned["spec"])


if __name__ == "__main__":
    unittest.main()
