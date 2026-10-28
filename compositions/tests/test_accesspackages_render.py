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

    def render(self, api, xr, observed, required=(), pending=False):
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
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = list(yaml.safe_load_all(result.stdout))
        if pending:
            self.assert_not_ready(rendered)
            self.assertTrue(any(
                c["type"] == "GroupResourcesReady" and c["status"] == "False"
                for c in rendered[0]["status"]["conditions"]
            ), rendered)
            self.assertTrue({
                r["metadata"]["annotations"]["crossplane.io/composition-resource-name"]
                for r in observed
            }.issubset({
                r["metadata"]["annotations"]["crossplane.io/composition-resource-name"]
                for r in rendered[1:]
            }), "Pending references must not prune existing resources")
        return rendered

    def approver_group(self, name, object_id):
        return {
            "apiVersion": "entraid.platform.example.org/v1alpha1",
            "kind": "XSecurityGroup",
            "metadata": {"name": name, "namespace": "developer-portal"},
            "status": {"objectId": object_id},
        }

    def assert_not_ready(self, rendered):
        self.assertFalse(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in rendered[0]["status"]["conditions"]
        ), rendered)

    def test_group_references_resolve_and_fail_closed(self):
        uid = "11111111-1111-4111-8111-111111111111"
        catalog = self.xr("accesspackagecatalog", uid)
        catalog["spec"].update({
            "groupIds": [GROUPS[0].upper()],
            "groupRefs": [{"name": "demo"}, {"name": "second-demo"}],
            "readerGroupIds": [],
        })
        groups = [
            self.approver_group(name, object_id)
            for name, object_id in zip(("demo", "second-demo"), GROUPS)
        ]
        for group in groups:
            group["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        catalog_observed = [self.observed("AccessPackageCatalog", "catalog", "catalog-id")]
        rendered = self.render("accesspackagecatalog", catalog, catalog_observed, groups)
        associations = [
            r for r in rendered if r["kind"] == "AccessPackageResourceCatalogAssociation"
        ]
        self.assertEqual(len(associations), 2)
        self.assertEqual(
            {a["spec"]["forProvider"]["resourceOriginId"] for a in associations},
            set(GROUPS),
        )
        self.assert_not_ready(rendered)
        for index, association in enumerate(associations):
            association["status"] = {
                "atProvider": {"id": f"association-{index}"},
                "conditions": [{"type": "Ready", "status": "True"}],
            }
        catalog_observed.extend(associations)
        rendered = self.render("accesspackagecatalog", catalog, catalog_observed, groups)
        catalog["status"] = rendered[0]["status"]
        self.assertTrue(all(a["ready"] and a["id"] for a in catalog["status"]["groupAssociations"]))
        self.assertTrue(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in catalog["status"]["conditions"]
        ))
        names = {a["metadata"]["name"] for a in associations}
        catalog["spec"]["groupRefs"].reverse()
        catalog["spec"]["groupIds"] = []
        reordered = self.render("accesspackagecatalog", catalog, catalog_observed, groups)
        self.assertEqual(names, {
            r["metadata"]["name"] for r in reordered
            if r["kind"] == "AccessPackageResourceCatalogAssociation"
        })

        package = self.xr("accesspackage", uid)
        package["spec"].update({
            "groupIds": [GROUPS[0].upper()],
            "groupRefs": [{"name": "demo"}, {"name": "second-demo"}],
        })
        package_observed = [
            self.observed("AccessPackage", "accessPackage", "package-id"),
            self.observed("AccessPackageAssignmentPolicy", "assignmentPolicy", "policy-id"),
        ]
        rendered = self.render("accesspackage", package, package_observed, [catalog, *groups])
        package_associations = [
            r for r in rendered if r["kind"] == "AccessPackageResourcePackageAssociation"
        ]
        self.assertEqual(len(package_associations), 2)
        self.assertFalse(any(
            r["kind"] == "AccessPackageResourceCatalogAssociation" for r in rendered
        ))
        self.assert_not_ready(rendered)
        for association in package_associations:
            association["status"] = {
                "atProvider": {"id": "package-association-id"},
                "conditions": [{"type": "Ready", "status": "True"}],
            }
        package_observed.extend(package_associations)
        ready = self.render("accesspackage", package, package_observed, [catalog, *groups])
        package["status"] = ready[0]["status"]
        self.assertTrue(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in ready[0]["status"]["conditions"]
        ))
        # Switching IDs to references keeps the existing composed resource keys.
        package["spec"]["groupIds"] = []
        package["spec"]["groupRefs"].reverse()
        reordered = self.render("accesspackage", package, package_observed, [catalog, *groups])
        self.assertEqual(
            {a["metadata"]["name"] for a in package_associations},
            {r["metadata"]["name"] for r in reordered
             if r["kind"] == "AccessPackageResourcePackageAssociation"},
        )

        for state in ("missing", "no-id", "no-status", "unready", "wrong-namespace"):
            required_groups = copy.deepcopy(groups)
            if state == "missing":
                required_groups.pop()
            elif state == "no-id":
                required_groups[1]["status"].pop("objectId")
            elif state == "no-status":
                required_groups[1].pop("status")
            elif state == "unready":
                required_groups[1]["status"]["conditions"][0]["status"] = "False"
            else:
                required_groups[1]["metadata"]["namespace"] = "another-team"
            with self.subTest(state=state):
                self.render(
                    "accesspackagecatalog", catalog, catalog_observed,
                    required_groups, pending=True,
                )
                self.render(
                    "accesspackage", package, package_observed,
                    [catalog, *required_groups], pending=True,
                )
                self.assert_not_ready(self.render(
                    "accesspackagecatalog", catalog, [], required_groups,
                ))
                self.assert_not_ready(self.render(
                    "accesspackage", package, [], [catalog, *required_groups],
                ))

        for state in ("missing", "pending", "unready"):
            required_catalog = copy.deepcopy(catalog)
            if state == "missing":
                required_catalog["status"]["groupAssociations"].pop()
            elif state == "pending":
                required_catalog["status"]["groupAssociations"][0]["id"] = ""
            else:
                required_catalog["status"]["groupAssociations"][0]["ready"] = False
            with self.subTest(catalog_association=state):
                self.render(
                    "accesspackage", package, package_observed,
                    [required_catalog, *groups], pending=True,
                )
        self.render("accesspackage", package, package_observed, groups, pending=True)

        # A dependency recovering restores readiness without changing associations.
        recovered = self.render("accesspackage", package, package_observed, [catalog, *groups])
        self.assertTrue(any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in recovered[0]["status"]["conditions"]
        ))

        # Removing the requested reference is intentional, unlike a missing group.
        package["spec"]["groupRefs"] = [{"name": "demo"}]
        removed = self.render("accesspackage", package, package_observed, [catalog, *groups])
        self.assertEqual(sum(
            r["kind"] == "AccessPackageResourcePackageAssociation" for r in removed
        ), 1)
        catalog["spec"]["groupRefs"] = [{"name": "demo"}]
        removed = self.render("accesspackagecatalog", catalog, catalog_observed, groups)
        self.assertEqual(sum(
            r["kind"] == "AccessPackageResourceCatalogAssociation" for r in removed
        ), 1)

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
                self.render("accesspackage", package, package_observed, [required], pending=True)
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
