import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent / "apis/entraid/securitygroup"


class SecurityGroupCompositionTests(unittest.TestCase):
    def setUp(self):
        self.xrd = yaml.safe_load((ROOT / "definition.yaml").read_text())
        self.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())

    def test_declared_membership_remains_the_default(self):
        spec = self.xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]
        group = self.composition["spec"]["pipeline"][0]["input"]["resources"][1]

        self.assertEqual(spec["properties"]["membershipMode"]["default"], "Declared")
        self.assertEqual(
            spec["properties"]["membershipMode"]["enum"], ["Declared", "External"]
        )
        self.assertIn(
            "spec.members.exists",
            group["template"]["spec"]["forProvider"]["members"],
        )
        self.assertNotIn("managementPolicies", group["template"]["spec"])
        self.assertNotIn("membershipMode", group["template"]["spec"]["forProvider"]["members"])

    def test_external_membership_is_unmanaged_and_rejects_declared_members(self):
        version = self.xrd["spec"]["versions"][0]
        validations = version["schema"]["openAPIV3Schema"]["properties"]["spec"][
            "x-kubernetes-validations"
        ]
        group = self.composition["spec"]["pipeline"][0]["input"]["resources"][1]
        membership = self.composition["spec"]["pipeline"][1]
        template = membership["input"]["inline"]["template"]

        self.assertEqual(
            membership["functionRef"]["name"], "function-go-templating"
        )
        self.assertIn('membershipMode "External"', template)
        self.assertIn('unset $group.spec.forProvider "members"', template)
        self.assertIn(
            '(list "Observe" "Create" "Update" "Delete")',
            template,
        )
        self.assertNotIn("LateInitialize", template)
        self.assertEqual(
            validations[0]["rule"],
            "self.membershipMode != 'External' || !has(self.members) || size(self.members) == 0",
        )
        self.assertNotIn("initProvider", group["template"]["spec"])
        self.assertNotIn("? null", str(group["template"]))
        self.assertNotIn(": null", str(group["template"]))


if __name__ == "__main__":
    unittest.main()
