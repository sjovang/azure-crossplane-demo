"""Opt-in tests against the pinned functions and provider OpenAPI schemas."""

import copy
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
APIS = ROOT / "apis/entraid"
GROUPS = [
    "4c0b5a4c-f623-4c48-a51d-d92752c9cf2f",
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
]
API = "identitygovernance.azuread.m.upbound.io/v1beta1"


@unittest.skipUnless(
    os.environ.get("RUN_ACCESSPACKAGE_RENDER") == "1",
    "Set RUN_ACCESSPACKAGE_RENDER=1; requires Docker, crossplane and gh.",
)
class AccessPackageRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "tests/.accesspackage-render"
        cls.work.mkdir()
        cls.addClassCleanup(shutil.rmtree, cls.work)
        schemas = cls.work / "schemas"
        schemas.mkdir()
        provider = yaml.safe_load((
            ROOT.parent / "clusters/base/crossplane/providers/azure/provider-azuread.yaml"
        ).read_text())
        provider_version = provider["spec"]["package"].rsplit(":", 1)[1]
        definitions = []
        for api in ("accesspackage", "accesspackagecatalog", "securitygroup"):
            xrd = yaml.safe_load((APIS / api / "definition.yaml").read_text())
            definitions.append((
                xrd["spec"]["names"]["kind"],
                "entraid.platform.example.org/v1alpha1",
                xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"],
            ))
        for kind in (
            "AccessPackage", "AccessPackageCatalog", "AccessPackageCatalogRoleAssignment",
            "AccessPackageResourceCatalogAssociation",
            "AccessPackageResourcePackageAssociation", "AccessPackageAssignmentPolicy",
        ):
            plural = kind.lower().removesuffix("y") + (
                "ies" if kind.endswith("y") else "s"
            )
            path = (
                "repos/upbound/provider-azuread/contents/package/crds/"
                f"identitygovernance.azuread.m.upbound.io_{plural}.yaml?ref={provider_version}"
            )
            raw = subprocess.check_output(
                ["gh", "api", path, "-H", "Accept: application/vnd.github.raw+json"],
                text=True,
            )
            crd = yaml.safe_load(raw)
            version = next(v for v in crd["spec"]["versions"] if v["name"] == "v1beta1")
            definitions.append((kind, API, version["schema"]["openAPIV3Schema"]))
        components = {}
        for kind, api_version, schema in definitions:
            group, version = api_version.split("/")
            schema["properties"].update({
                "apiVersion": {"type": "string"},
                "kind": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        **{field: {"type": "string"} for field in ("name", "namespace", "uid")},
                        **{
                            field: {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            }
                            for field in ("annotations", "labels")
                        },
                    },
                },
            })
            schema["x-kubernetes-group-version-kind"] = [
                {"group": group, "version": version, "kind": kind}
            ]
            components[kind] = schema
        (schemas / "schemas.json").write_text(json.dumps({
            "openapi": "3.0.0", "info": {"title": "Test schemas", "version": "1"},
            "paths": {}, "components": {"schemas": components},
        }))
        functions = []
        for name in ("kro", "go-templating", "auto-ready"):
            functions.append(yaml.safe_load(
                (ROOT.parent / f"clusters/base/crossplane/functions/{name}.yaml").read_text()
            ))
        cls.write("functions.yaml", functions)

    @classmethod
    def write(cls, name, documents):
        path = cls.work / name
        path.write_text(yaml.safe_dump_all(documents, explicit_start=True))
        return path

    def xr(self, api, uid):
        xr = yaml.safe_load((ROOT / f"tests/entraid/{api}/xr.yaml").read_text())
        xr["metadata"]["uid"] = uid
        xrd = yaml.safe_load((APIS / api / "definition.yaml").read_text())
        properties = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["properties"]
        for field, schema in properties.items():
            if "default" in schema:
                xr["spec"].setdefault(field, schema["default"])
        return xr

    def observed(self, kind, key, provider_id, spec=None):
        return {
            "apiVersion": API, "kind": kind,
            "metadata": {
                "name": key.removeprefix("groupCatalogAssociations-").lower(),
                "namespace": "developer-portal",
                "annotations": {"crossplane.io/composition-resource-name": key},
            },
            "spec": {"forProvider": spec or {}},
            "status": {
                "atProvider": {"id": provider_id},
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }

    def render(self, api, xr, observed, required=(), failure=False):
        if api == "accesspackage":
            required = [
                *required,
                self.approver_group("engineering-approvers", "approver-id"),
                self.approver_group("engineering-reviewers", "reviewer-id"),
            ]
        result = subprocess.run([
            "crossplane", "render", str(self.write("xr.yaml", [xr])),
            str(APIS / api / "composition.yaml"), str(self.work / "functions.yaml"),
            "--observed-resources", str(self.write("observed.yaml", observed)),
            "--required-resources", str(self.write("required.yaml", required)),
            "--required-schemas", str(self.work / "schemas"),
            "--crossplane-version", "v2.4.0",
            "--cache-dir", str(self.work / "cache"), "--timeout", "2m",
        ], capture_output=True, text=True, cwd=ROOT.parent, timeout=180)
        if failure:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Requested groups must all be registered and Ready", result.stderr)
            return []
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(yaml.safe_load_all(result.stdout))

    def approver_group(self, name, object_id):
        return {
            "apiVersion": "entraid.platform.example.org/v1alpha1",
            "kind": "XSecurityGroup",
            "metadata": {"name": name, "namespace": "developer-portal"},
            "status": {"objectId": object_id},
        }

    def test_shared_catalog_lifecycle_and_strict_group_selection(self):
        uid = "11111111-1111-4111-8111-111111111111"
        catalog = self.xr("accesspackagecatalog", uid)
        catalog["spec"]["groupIds"] = GROUPS[:]
        observed = [self.observed("AccessPackageCatalog", "catalog", "catalog-id")]
        for i, group in enumerate(GROUPS):
            association = self.observed(
                "AccessPackageResourceCatalogAssociation",
                f"groupCatalogAssociations-catalog-{uid}.{group}",
                f"association-{i}",
                {"catalogId": "catalog-id", "resourceOriginId": group,
                 "resourceOriginSystem": "AadGroup"},
            )
            association["metadata"]["labels"] = {"kro.run/collection-index": str(i)}
            observed.append(association)
        rendered = self.render("accesspackagecatalog", catalog, observed)
        catalog["status"] = rendered[0]["status"]
        self.assertIn("groupAssociations", catalog["status"], rendered)
        self.assertEqual(catalog["status"]["groupAssociations"], [
            {"groupId": group, "id": f"association-{i}", "ready": True}
            for i, group in enumerate(GROUPS)
        ])
        catalog_associations = {
            r["metadata"]["name"] for r in rendered
            if r["kind"] == "AccessPackageResourceCatalogAssociation"
        }
        self.assertEqual(len(catalog_associations), 2)
        for resource in rendered[1:]:
            self.assertEqual(
                resource["metadata"]["ownerReferences"][0]["kind"],
                "XAccessPackageCatalog",
            )
        catalog["spec"]["groupIds"].reverse()
        reordered = self.render("accesspackagecatalog", catalog, observed)
        self.assertEqual(catalog_associations, {
            r["metadata"]["name"] for r in reordered
            if r["kind"] == "AccessPackageResourceCatalogAssociation"
        })

        package_names = []
        for package_uid in (uid, "22222222-2222-4222-8222-222222222222"):
            package = self.xr("accesspackage", package_uid)
            package["metadata"]["name"] = "package-" + package_uid
            package_observed = [self.observed("AccessPackage", "accessPackage", "package-id")]
            rendered = self.render("accesspackage", package, package_observed, [catalog])
            self.assertFalse(any(
                r["kind"] in ("XAccessPackageCatalog", "AccessPackageResourceCatalogAssociation")
                for r in rendered[1:]
            ))
            associations = [
                r for r in rendered if r["kind"] == "AccessPackageResourcePackageAssociation"
            ]
            self.assertEqual(len(associations), 1)
            policy = next(
                r for r in rendered if r["kind"] == "AccessPackageAssignmentPolicy"
            )
            approval_stage = policy["spec"]["forProvider"]["approvalSettings"][
                "approvalStage"
            ][0]
            self.assertEqual(
                approval_stage["primaryApprover"][0]["objectId"], "approver-id"
            )
            self.assertEqual(
                approval_stage["alternativeApprover"][0]["objectId"], "reviewer-id"
            )
            self.assertEqual(
                policy["spec"]["forProvider"]["assignmentReviewSettings"]["reviewer"],
                [
                    {"objectId": "approver-id", "subjectType": "GroupMembers"},
                    {"objectId": "reviewer-id", "subjectType": "GroupMembers"},
                ],
            )
            self.assertEqual(
                associations[0]["metadata"]["ownerReferences"][0]["uid"],
                package_uid,
            )
            self.assertEqual(
                associations[0]["spec"]["forProvider"]["catalogResourceAssociationId"],
                "association-0",
            )
            package_names.append(associations[0]["metadata"]["name"])
        self.assertNotEqual(*package_names)

        package["spec"]["groupIds"] = GROUPS[:]
        rendered = self.render("accesspackage", package, package_observed, [catalog])
        names = {
            r["metadata"]["name"] for r in rendered
            if r["kind"] == "AccessPackageResourcePackageAssociation"
        }
        self.assertEqual(len(names), 2)
        package["spec"]["groupIds"] = list(reversed([g.upper() for g in GROUPS]))
        reordered = self.render("accesspackage", package, package_observed, [catalog])
        self.assertEqual(names, {
            r["metadata"]["name"] for r in reordered
            if r["kind"] == "AccessPackageResourcePackageAssociation"
        })
        package["spec"]["groupIds"] = [GROUPS[0]]
        association = copy.deepcopy(next(
            r for r in rendered if r["kind"] == "AccessPackageResourcePackageAssociation"
            and r["spec"]["forProvider"]["catalogResourceAssociationId"] == "association-0"
        ))
        association["status"] = {
            "atProvider": {"id": "package-association-id"},
            "conditions": [{"type": "Ready", "status": "True"}],
        }
        package_observed += [
            self.observed("AccessPackageAssignmentPolicy", "assignmentPolicy", "policy-id"),
            association,
        ]
        ready_package = self.render("accesspackage", package, package_observed, [catalog])
        self.assertTrue(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in ready_package[0]["status"]["conditions"]
        ))

        for state in ("missing", "pending", "unready", "no-status", "partial"):
            with self.subTest(state=state):
                required = copy.deepcopy(catalog)
                if state == "missing":
                    required["status"]["groupAssociations"] = []
                elif state == "pending":
                    required["status"]["groupAssociations"][0]["id"] = ""
                elif state == "unready":
                    required["status"]["groupAssociations"][0]["ready"] = False
                elif state == "partial":
                    package["spec"]["groupIds"] = GROUPS[:]
                    required["status"]["groupAssociations"].pop()
                else:
                    required.pop("status")
                self.render("accesspackage", package, package_observed, [required], failure=True)
                package["spec"]["groupIds"] = [GROUPS[0]]

        pending_catalog = copy.deepcopy(catalog)
        pending_catalog["status"]["groupAssociations"] = []
        initial = self.render("accesspackage", package, [], [pending_catalog])
        self.assertIn(
            {"type": "GroupResourcesReady", "status": "False",
             "reason": "WaitingForCatalogGroups",
             "message": "All requested groups must be registered and Ready in catalogRef.",
             "lastTransitionTime": "2024-01-01T00:00:00Z"},
            initial[0]["status"]["conditions"],
        )
        self.assertFalse(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in initial[0]["status"]["conditions"]
        ))
        absent_catalog = self.render("accesspackage", package, [])
        self.assertFalse(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in absent_catalog[0]["status"]["conditions"]
        ))

        package["spec"]["groupIds"] = []
        rendered = self.render("accesspackage", package, package_observed, [catalog])
        self.assertFalse(any(
            r["kind"] == "AccessPackageResourcePackageAssociation" for r in rendered
        ))
        catalog["spec"]["groupIds"] = []
        empty_catalog = self.render("accesspackagecatalog", catalog, observed[:1])
        self.assertEqual(empty_catalog[0]["status"].get("groupAssociations", []), [])
        self.assertFalse(any(
            r["kind"] == "AccessPackageResourceCatalogAssociation" for r in empty_catalog
        ))
