import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class EnterpriseAppCompositionTests(unittest.TestCase):
    def setUp(self):
        self.xrd = yaml.safe_load((ROOT / "xrd.yaml").read_text())
        self.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())
        self.xr = yaml.safe_load((ROOT / "tests/xr.yaml").read_text())
        self.expected = yaml.safe_load((ROOT / "tests/expected.yaml").read_text())

    def test_public_api_contract(self):
        version = self.xrd["spec"]["versions"][0]
        spec = version["schema"]["openAPIV3Schema"]["properties"]["spec"]

        self.assertEqual(self.xrd["spec"]["scope"], "Namespaced")
        self.assertEqual(spec["required"], ["name", "redirectUris"])
        self.assertEqual(spec["properties"]["rotationInterval"]["default"], "720h")
        self.assertEqual(
            spec["properties"]["credentialValidity"]["default"], "1440h"
        )

    def test_resource_graph_snapshot(self):
        graph = self.composition["spec"]["pipeline"][0]["input"]
        resources = graph["resources"]
        by_id = {resource["id"]: resource for resource in resources}

        self.assertEqual(
            [resource["id"] for resource in resources],
            self.expected["resourceIds"],
        )
        self.assertEqual(graph["status"], self.expected["status"])
        self.assertEqual(
            by_id["application"]["template"]["spec"]["forProvider"]["web"][
                "redirectUris"
            ],
            self.expected["redirectUris"],
        )
        self.assertFalse(
            by_id["servicePrincipal"]["template"]["spec"]["forProvider"][
                "appRoleAssignmentRequired"
            ]
        )
        self.assertEqual(
            by_id["rotationExternalSecret"]["template"]["spec"]["refreshInterval"],
            self.expected["rotation"]["interval"],
        )
        self.assertEqual(by_id["rotationGenerator"]["readyWhen"], ["${true}"])
        self.assertEqual(
            by_id["applicationPassword"]["template"]["spec"]["forProvider"][
                "endDateRelative"
            ],
            self.expected["rotation"]["validity"],
        )
        self.assertEqual(
            by_id["applicationPassword"]["template"]["spec"]["forProvider"][
                "rotateWhenChanged"
            ]["rotation"],
            self.expected["rotation"]["trigger"],
        )

    def test_example_supplies_required_fields(self):
        required = self.xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["required"]
        self.assertTrue(all(field in self.xr["spec"] for field in required))


if __name__ == "__main__":
    unittest.main()
