import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ResourceGroupCompositionTests(unittest.TestCase):
    def setUp(self):
        self.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())
        self.xrd = yaml.safe_load((ROOT / "xrd.yaml").read_text())
        resource = self.composition["spec"]["pipeline"][0]["input"]["resources"][0]
        self.output_patches = {
            patch["toFieldPath"]: patch
            for patch in resource["patches"]
            if patch["type"] == "ToCompositeFieldPath"
        }

    def test_resource_group_name_comes_from_external_name_annotation(self):
        # The Azure provider exposes no status.atProvider.name; downstream
        # Key Vault creation depends on this XR output.
        self.assertEqual(
            self.output_patches["status.name"]["fromFieldPath"],
            "metadata.annotations[crossplane.io/external-name]",
        )

    def test_resource_group_id_still_comes_from_observed_azure_resource(self):
        self.assertEqual(
            self.output_patches["status.id"]["fromFieldPath"],
            "status.atProvider.id",
        )

    def test_resource_group_outputs_are_declared_in_xrd(self):
        status = self.xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["status"]["properties"]
        for field_path in self.output_patches:
            self.assertEqual(status[field_path.removeprefix("status.")]["type"], "string")


if __name__ == "__main__":
    unittest.main()
