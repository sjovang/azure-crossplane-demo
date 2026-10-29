import re
import unittest
from pathlib import Path

import yaml


TESTS = Path(__file__).resolve().parent
APIS = TESTS.parent / "apis/entraid"
FIXTURES = TESTS / "entraid"


class AccessPackageCatalogCompositionTests(unittest.TestCase):
    def setUp(self):
        root = APIS / "accesspackagecatalog"
        fixtures = FIXTURES / "accesspackagecatalog"
        self.xrd = yaml.safe_load((root / "definition.yaml").read_text())
        self.composition = yaml.safe_load((root / "composition.yaml").read_text())
        self.xr = yaml.safe_load((fixtures / "xr.yaml").read_text())
        self.expected = yaml.safe_load((fixtures / "expected.yaml").read_text())

    def test_public_api_contract(self):
        version = self.xrd["spec"]["versions"][0]
        spec = version["schema"]["openAPIV3Schema"]["properties"]["spec"]

        self.assertEqual(self.xrd["spec"]["scope"], "Namespaced")
        self.assertEqual(spec["required"], ["description", "name"])
        self.assertFalse(spec["properties"]["externallyVisible"]["default"])
        self.assertEqual(spec["properties"]["readerGroupIds"]["default"], [])
        self.assertEqual(spec["properties"]["groupIds"]["default"], [])
        assert_group_refs(self, spec)
        self.assertEqual(
            spec["properties"]["groupIds"]["x-kubernetes-list-type"], "set"
        )
        self.assertNotIn("uniqueItems", spec["properties"]["groupIds"])
        status = version["schema"]["openAPIV3Schema"]["properties"]["status"]
        self.assertEqual(
            set(status["properties"]["groupAssociations"]["items"]["properties"]),
            {"groupId", "id", "ready"},
        )

    def test_resource_graph_snapshot(self):
        graph = self.composition["spec"]["pipeline"][0]["input"]
        resources = graph["resources"]
        by_id = {resource["id"]: resource for resource in resources}

        self.assertEqual(
            [
                (resource["template"]["apiVersion"], resource["template"]["kind"])
                for resource in resources
            ],
            [
                (
                    "identitygovernance.azuread.m.upbound.io/v1beta1",
                    "AccessPackageCatalog",
                ),
                (
                    "identitygovernance.azuread.m.upbound.io/v1beta1",
                    "AccessPackageCatalogRoleAssignment",
                ),
            ],
        )
        self.assertEqual(
            [resource["id"] for resource in resources],
            self.expected["resourceIds"],
        )
        self.assertEqual(graph["status"], self.expected["status"])
        self.assertEqual(
            by_id["catalogReaderRoleAssignments"]["forEach"][0]["groupId"],
            "${schema.spec.readerGroupIds}",
        )
        self.assertEqual(
            by_id["catalogReaderRoleAssignments"]["template"]["metadata"]["name"],
            '${("reader-" + schema.metadata.uid + "." + groupId).lowerAscii()}',
        )
        self.assertEqual(
            by_id["catalogReaderRoleAssignments"]["template"]["spec"]["forProvider"][
                "roleId"
            ],
            self.expected["catalogReaderRoleId"],
        )
        self.assertTrue(
            by_id["catalogReaderRoleAssignments"]["template"]["spec"]["forProvider"][
                "catalogId"
            ]
        )
        self.assertTrue(all(field in self.xr["spec"] for field in spec_required(self.xrd)))


class AccessPackageCompositionTests(unittest.TestCase):
    def setUp(self):
        root = APIS / "accesspackage"
        fixtures = FIXTURES / "accesspackage"
        self.xrd = yaml.safe_load((root / "definition.yaml").read_text())
        self.composition = yaml.safe_load((root / "composition.yaml").read_text())
        self.xr = yaml.safe_load((fixtures / "xr.yaml").read_text())
        self.expected = yaml.safe_load((fixtures / "expected.yaml").read_text())

    def test_public_api_contract(self):
        version = self.xrd["spec"]["versions"][0]
        spec = version["schema"]["openAPIV3Schema"]["properties"]["spec"]

        self.assertEqual(self.xrd["spec"]["scope"], "Namespaced")
        self.assertIn("catalogRef", spec["required"])
        self.assertIn("primaryApproverGroupRef", spec["required"])
        self.assertIn("secondaryApproverGroupRef", spec["required"])
        self.assertEqual(spec["properties"]["catalogRef"]["required"], ["name"])
        self.assertEqual(
            spec["properties"]["primaryApproverGroupRef"]["required"], ["name"]
        )
        self.assertEqual(
            spec["properties"]["secondaryApproverGroupRef"]["required"], ["name"]
        )
        self.assertNotIn("catalogId", spec["properties"])
        self.assertEqual(spec["properties"]["groupIds"]["default"], [])
        assert_group_refs(self, spec)
        self.assertEqual(
            spec["properties"]["groupIds"]["x-kubernetes-list-type"], "set"
        )
        self.assertNotIn("uniqueItems", spec["properties"]["groupIds"])
        self.assertEqual(
            spec["properties"]["requestorScope"]["default"],
            "AllExistingDirectoryMemberUsers",
        )
        self.assertIn(
            "AllExternalSubjects",
            spec["properties"]["requestorScope"]["pattern"],
        )
        self.assertEqual(spec["properties"]["reviewFrequency"]["default"], "quarterly")
        self.assertEqual(spec["properties"]["approvalTimeoutDays"]["default"], 14)
        self.assertEqual(spec["properties"]["escalationAfterDays"]["default"], 7)
        self.assertEqual(spec["properties"]["reviewDurationDays"]["default"], 14)
        self.assertTrue(
            all(field in self.xr["spec"] for field in spec["required"])
        )

    def test_resource_graph_snapshot(self):
        graph = self.composition["spec"]["pipeline"][0]["input"]
        resources = graph["resources"]
        by_id = {resource["id"]: resource for resource in resources}

        self.assertEqual(
            [
                (resource["template"]["apiVersion"], resource["template"]["kind"])
                for resource in resources if "template" in resource
            ],
            [
                (
                    "identitygovernance.azuread.m.upbound.io/v1beta1",
                    "AccessPackage",
                ),
                (
                    "identitygovernance.azuread.m.upbound.io/v1beta1",
                    "AccessPackageAssignmentPolicy",
                ),
            ],
        )
        self.assertEqual(
            [resource["id"] for resource in resources],
            self.expected["resourceIds"],
        )
        self.assertEqual(graph["status"], self.expected["status"])
        self.assertEqual(
            by_id["primaryApproverGroup"]["externalRef"],
            {
                "apiVersion": "entraid.platform.example.org/v1alpha1",
                "kind": "XSecurityGroup",
                "metadata": {"name": "${schema.spec.primaryApproverGroupRef.name}"},
            },
        )
        self.assertEqual(
            by_id["secondaryApproverGroup"]["externalRef"],
            {
                "apiVersion": "entraid.platform.example.org/v1alpha1",
                "kind": "XSecurityGroup",
                "metadata": {"name": "${schema.spec.secondaryApproverGroupRef.name}"},
            },
        )
        policy = by_id["assignmentPolicy"]["template"]["spec"]["forProvider"]
        self.assertEqual(
            self.expected["requestorScopes"],
            [
                "AllExistingDirectoryMemberUsers",
                "AllExternalSubjects",
            ],
        )
        self.assertEqual(
            policy["requestorSettings"]["scopeType"],
            "${schema.spec.requestorScope}",
        )
        self.assertTrue(policy["requestorSettings"]["requestsAccepted"])
        self.assertEqual(
            policy["approvalSettings"]["approvalStage"][0][
                "alternativeApprovalEnabled"
            ],
            True,
        )
        self.assertEqual(
            policy["approvalSettings"]["approvalStage"][0][
                "approvalTimeoutInDays"
            ],
            self.expected["approvalTimeoutDays"],
        )
        self.assertEqual(
            policy["approvalSettings"]["approvalStage"][0][
                "enableAlternativeApprovalInDays"
            ],
            self.expected["escalationAfterDays"],
        )
        self.assertEqual(
            policy["approvalSettings"]["approvalStage"][0][
                "alternativeApprover"
            ][0]["objectId"],
            "${secondaryApproverGroup.status.objectId}",
        )
        self.assertEqual(
            policy["approvalSettings"]["approvalStage"][0]["primaryApprover"][0][
                "objectId"
            ],
            "${primaryApproverGroup.status.objectId}",
        )
        self.assertEqual(
            policy["assignmentReviewSettings"]["reviewFrequency"],
            self.expected["reviewFrequency"],
        )
        self.assertEqual(
            policy["assignmentReviewSettings"]["reviewType"],
            self.expected["reviewType"],
        )
        self.assertEqual(
            policy["assignmentReviewSettings"]["durationInDays"],
            self.expected["reviewDurationDays"],
        )
        self.assertEqual(
            policy["assignmentReviewSettings"]["reviewer"],
            self.expected["reviewers"],
        )

        self.assertNotIn("groupCatalogAssociations", by_id)
        self.assertEqual(by_id["catalog"], {
            "id": "catalog",
            "externalRef": {
                "apiVersion": "entraid.platform.example.org/v1alpha1",
                "kind": "XAccessPackageCatalog",
                "metadata": {"name": "${schema.spec.catalogRef.name}"},
            },
        })
        self.assertEqual(
            by_id["accessPackage"]["template"]["spec"]["forProvider"]["catalogId"],
            "${catalog.status.id}",
        )

    def test_missing_groups_fail_closed_before_auto_ready(self):
        pipeline = self.composition["spec"]["pipeline"]
        self.assertEqual(
            [step["step"] for step in pipeline],
            ["kro", "check-group-resources", "auto-ready"],
        )
        guard = pipeline[1]
        self.assertEqual(guard["functionRef"]["name"], "function-go-templating")
        template = guard["input"]["inline"]["template"]
        self.assertIn("$xr.spec.groupIds", template)
        self.assertIn("$xr.spec.groupRefs", template)
        self.assertIn("namespace: {{ $xr.metadata.namespace | quote }}", template)
        self.assertIn("AccessPackageResourcePackageAssociation", template)
        self.assertIn('gotemplating.fn.crossplane.io/ready: "False"', template)
        self.assertIn("type: GroupResourcesReady", template)

    def test_pending_resources_exclude_observed_runtime_fields(self):
        for api in ("accesspackagecatalog", "accesspackage"):
            with self.subTest(api=api):
                composition = yaml.safe_load(
                    (APIS / api / "composition.yaml").read_text()
                )
                template = composition["spec"]["pipeline"][1]["input"]["inline"][
                    "template"
                ]
                self.assertIn(
                    'pick $resource.resource "apiVersion" "kind" "spec"', template
                )
                self.assertIn(
                    'pick $resource.resource.metadata "name" "namespace"', template
                )
                self.assertIn(
                    'dict "gotemplating.fn.crossplane.io/composition-resource-name" $key',
                    template,
                )
                self.assertNotIn("deepCopy $resource.resource", template)

    def test_team_demo_grants_only_the_external_membership_group(self):
        team = TESTS.parent.parent / "teams/crd-dev"
        groups = list(yaml.safe_load_all((team / "entraid.yaml").read_text()))
        catalog, package = yaml.safe_load_all(
            (team / "accesspackages.yaml").read_text()
        )
        refs = [{"name": "crd-dev-access-demo"}]
        self.assertEqual(catalog["spec"]["groupRefs"], refs)
        self.assertEqual(package["spec"]["groupRefs"], refs)
        group = next(g for g in groups if g["metadata"]["name"] == refs[0]["name"])
        self.assertEqual(group["spec"]["membershipMode"], "External")
        self.assertFalse(group["spec"].get("members"))
        for field in ("primaryApproverGroupRef", "secondaryApproverGroupRef"):
            self.assertNotEqual(package["spec"][field], refs[0])

    def test_shared_catalog_example_registers_the_group_once(self):
        example = list(yaml.safe_load_all(
            (TESTS.parent / "examples/entraid-accesspackages.yaml").read_text()
        ))
        catalog, *packages = example
        self.assertEqual(catalog["kind"], "XAccessPackageCatalog")
        self.assertEqual(len(packages), 2)
        for package in packages:
            self.assertEqual(
                package["spec"]["catalogRef"]["name"], catalog["metadata"]["name"]
            )
            self.assertEqual(
                package["metadata"]["namespace"], catalog["metadata"]["namespace"]
            )
            self.assertEqual(package["spec"]["groupIds"], catalog["spec"]["groupIds"])

    def test_collection_names_are_stable_and_unique(self):
        xr_uids = (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
        groups = (
            "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB",
        )

        def names(prefix, uid, group_ids):
            return {
                group_id: f"{prefix}-{uid}.{group_id}".lower()
                for group_id in group_ids
            }

        associations = [
            {"spec": {"forProvider": {"resourceOriginId": group_id}}}
            for group_id in groups
        ]
        package_groups = [
            association["spec"]["forProvider"]["resourceOriginId"]
            for association in associations
        ]
        collection_groups = {
            "catalog": groups,
            "reader": groups,
            "package": package_groups,
        }
        collection_names = {}
        for prefix, group_ids in collection_groups.items():
            stable_names = names(prefix, xr_uids[0], group_ids)
            reordered_names = names(prefix, xr_uids[0], reversed(group_ids))
            other_xr_names = names(prefix, xr_uids[1], group_ids)
            self.assertEqual(stable_names, reordered_names)
            self.assertTrue(set(stable_names.values()).isdisjoint(other_xr_names.values()))
            collection_names[prefix] = stable_names

        for prefix, values in collection_names.items():
            for other_prefix, other_values in collection_names.items():
                if prefix != other_prefix:
                    self.assertTrue(set(values.values()).isdisjoint(other_values.values()))
        all_names = (
            *(name for values in collection_names.values() for name in values.values()),
            *(
                name
                for prefix, group_ids in collection_groups.items()
                for name in names(prefix, xr_uids[1], group_ids).values()
            ),
        )
        for name in all_names:
            self.assertLessEqual(len(name), 253)
            self.assertTrue(
                all(
                    len(label) <= 63
                    and re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", label)
                    for label in name.split(".")
                )
            )


def spec_required(xrd):
    return (
        xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"][
            "spec"
        ]["required"]
    )


def assert_group_refs(test, spec):
    refs = spec["properties"]["groupRefs"]
    test.assertEqual(refs["default"], [])
    test.assertEqual(refs["x-kubernetes-list-type"], "map")
    test.assertEqual(refs["x-kubernetes-list-map-keys"], ["name"])
    test.assertEqual(refs["items"]["required"], ["name"])
    test.assertEqual(refs["items"]["properties"]["name"]["minLength"], 1)


if __name__ == "__main__":
    unittest.main()
