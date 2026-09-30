import copy
import json
import subprocess
import unittest
from pathlib import Path

import yaml


AZURE = Path(__file__).resolve().parents[1]
ROOT = AZURE.parents[1]
EXAMPLES = list(yaml.safe_load_all((ROOT / "teams/crd-dev/apps.yaml").read_text()))
CASES = {
    "appservice": "XAppService",
    "containerapp": "XContainerApp",
    "postgresql": "XDatabase",
}


class AppTemplateTests(unittest.TestCase):
    def test_team_examples(self):
        for directory, kind in CASES.items():
            with self.subTest(kind=kind):
                folder = AZURE / directory
                xr = next(doc for doc in EXAMPLES if doc.get("kind") == kind)
                xr["metadata"]["namespace"] = "crd-dev"
                xr["spec"]["location"] = "swedencentral"
                xrd = yaml.safe_load((folder / "xrd.yaml").read_text())
                properties = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
                for name, schema in properties.items():
                    if "default" in schema:
                        xr["spec"].setdefault(name, schema["default"])
                composition = yaml.safe_load((folder / "composition.yaml").read_text())
                template = composition["spec"]["pipeline"][0]["input"]["inline"]["template"]
                result = subprocess.run(
                    ["go", "run", str(AZURE / "tests/render.go")],
                    input=json.dumps({"template": template, "xr": xr}),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = list(yaml.safe_load_all(result.stdout))
                expected = list(yaml.safe_load_all((folder / "tests/expected.yaml").read_text()))
                self.assertEqual(actual, expected)

                alternate = copy.deepcopy(xr)
                alternate["spec"]["size"] = "medium"
                alternate_expected = copy.deepcopy(expected)
                if kind == "XAppService":
                    alternate["spec"]["envVars"] = {"DEMO": "enabled"}
                    alternate["spec"]["systemAssignedIdentity"] = True
                    alternate_expected[0]["spec"]["forProvider"]["skuName"] = "P1v3"
                    alternate_expected[1]["spec"]["forProvider"]["identity"] = {
                        "type": "SystemAssigned"
                    }
                    alternate_expected[1]["spec"]["forProvider"]["appSettings"]["DEMO"] = "enabled"
                elif kind == "XContainerApp":
                    alternate["spec"]["envVars"] = {"DEMO": "enabled"}
                    alternate_expected[1]["spec"]["forProvider"]["template"]["container"][0]["cpu"] = 0.5
                    alternate_expected[1]["spec"]["forProvider"]["template"]["container"][0]["memory"] = "1Gi"
                    alternate_expected[1]["spec"]["forProvider"]["template"]["container"][0]["env"] = [
                        {"name": "DEMO", "value": "enabled"}
                    ]
                else:
                    alternate["spec"]["databaseName"] = "alternate"
                    alternate["spec"]["allowAzureServices"] = True
                    alternate_expected[0]["spec"]["forProvider"]["skuName"] = "GP_Standard_D2s_v3"
                    alternate_expected[0]["spec"]["forProvider"]["storageMb"] = 131072
                    alternate_expected[1]["metadata"]["annotations"]["crossplane.io/external-name"] = "alternate"
                    alternate_expected.append(
                        {
                            "apiVersion": "dbforpostgresql.azure.m.upbound.io/v1beta1",
                            "kind": "FlexibleServerFirewallRule",
                            "metadata": {
                                "annotations": {
                                    "gotemplating.fn.crossplane.io/composition-resource-name": "allowAzureServices",
                                    "crossplane.io/external-name": "AllowAzureServices",
                                }
                            },
                            "spec": {
                                "providerConfigRef": {
                                    "kind": "ClusterProviderConfig",
                                    "name": "default",
                                },
                                "forProvider": {
                                    "serverIdSelector": {
                                        "matchControllerRef": True
                                    },
                                    "startIpAddress": "0.0.0.0",
                                    "endIpAddress": "0.0.0.0",
                                },
                            },
                        }
                    )
                alternate_result = subprocess.run(
                    ["go", "run", str(AZURE / "tests/render.go")],
                    input=json.dumps({"template": template, "xr": alternate}),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(alternate_result.returncode, 0, alternate_result.stderr)
                self.assertEqual(list(yaml.safe_load_all(alternate_result.stdout)), alternate_expected)


if __name__ == "__main__":
    unittest.main()
