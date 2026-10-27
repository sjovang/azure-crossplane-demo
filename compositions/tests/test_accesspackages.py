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
        self.assertTrue(spec["properties"]["groupIds"]["uniqueItems"])
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
                    "AccessPackageResourceCatalogAssociation",
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
        association = by_id["groupCatalogAssociations"]
        self.assertEqual(association["forEach"], [{"groupId": "${schema.spec.groupIds}"}])
        self.assertEqual(
            association["template"]["metadata"]["name"],
            '${("catalog-" + schema.metadata.uid + "." + groupId).lowerAscii()}',
        )
        self.assertEqual(
            association["template"]["metadata"]["namespace"],
            "${schema.metadata.namespace}",
        )
        self.assertEqual(
            association["template"]["spec"]["forProvider"],
            {
                "catalogId": "${catalog.status.atProvider.id}",
                "resourceOriginId": "${groupId}",
                "resourceOriginSystem": "AadGroup",
            },
        )
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
        self.assertEqual(spec["properties"]["catalogRef"]["required"], ["name"])
        self.assertNotIn("catalogId", spec["properties"])
        self.assertEqual(spec["properties"]["groupIds"]["default"], [])
        self.assertTrue(spec["properties"]["groupIds"]["uniqueItems"])
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
                    "AccessPackageResourcePackageAssociation",
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
            "${schema.spec.secondaryApproverGroupId}",
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

        package_association = by_id["groupPackageAssociations"]
        self.assertEqual(
            package_association["forEach"][0]["groupId"],
            "${schema.spec.groupIds}",
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
        self.assertIn(
            'a.id != "" && a.ready)[0].id',
            package_association["template"]["spec"]["forProvider"][
                "catalogResourceAssociationId"
            ],
        )
        self.assertEqual(
            package_association["template"]["spec"]["forProvider"]["accessType"],
            self.expected["accessType"],
        )
        self.assertEqual(
            package_association["template"]["spec"]["forProvider"][
                "catalogResourceAssociationId"
            ],
            self.expected["catalogAssociationId"],
        )
        self.assertEqual(
            package_association["template"]["metadata"]["name"],
            '${("package-" + schema.metadata.uid + "." + groupId).lowerAscii()}',
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
        self.assertIn("len ($xr.spec.groupIds | default list)", template)
        self.assertIn("AccessPackageResourcePackageAssociation", template)
        self.assertIn('fail "Requested groups must all be registered and Ready', template)
        self.assertIn('gotemplating.fn.crossplane.io/ready: "False"', template)
        self.assertIn("type: GroupResourcesReady", template)

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


if __name__ == "__main__":
    unittest.main()
