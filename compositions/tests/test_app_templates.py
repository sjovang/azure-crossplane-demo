import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

import yaml


TESTS = Path(__file__).resolve().parent
APIS = TESTS.parent / "apis"
REPO = TESTS.parents[1]
RENDER = TESTS / "render.go"
EXAMPLES = list(yaml.safe_load_all((REPO / "teams/crd-dev/apps.yaml").read_text()))
CASES = {
    "appservice": "XAppService",
    "containerapp": "XContainerApp",
    "postgresql": "XDatabase",
}


class AppTemplateTests(unittest.TestCase):
    def test_postgresql_compute_override_preserves_storage_and_region(self):
        folder = APIS / "azure/postgresql"
        composition = yaml.safe_load((folder / "composition.yaml").read_text())
        template = composition["spec"]["pipeline"][0]["input"]["inline"]["template"]
        xr = copy.deepcopy(next(doc for doc in EXAMPLES if doc.get("kind") == "XDatabase"))
        xr["spec"].update({
            "location": "swedencentral", "postgresVersion": "16",
            "skuName": "B_Standard_B2s",
        })
        for size, storage in (("small", 32768), ("medium", 131072), ("large", 262144)):
            with self.subTest(size=size):
                xr["spec"]["size"] = size
                result = subprocess.run(
                    ["go", "run", str(RENDER)],
                    input=json.dumps({"template": template, "xr": xr}),
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                server = list(yaml.safe_load_all(result.stdout))[0]["spec"]["forProvider"]
                self.assertEqual(server["skuName"], "B_Standard_B2s")
                self.assertEqual(server["storageMb"], storage)
                self.assertEqual(server["location"], "swedencentral")

    def test_postgresql_sku_overrides_share_validated_optional_contract(self):
        fields = []
        for api, field in (
            ("azure/postgresql", "skuName"),
            ("environments/webapplication", "databaseSkuName"),
        ):
            xrd = yaml.safe_load((APIS / api / "definition.yaml").read_text())
            spec = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
                "properties"
            ]["spec"]
            self.assertNotIn(field, spec["required"])
            schema = spec["properties"][field]
            self.assertEqual(schema["default"], "")
            fields.append(schema["pattern"])
            for value in ("", "B_Standard_B2s", "GP_Standard_D2ds_v5"):
                self.assertIsNotNone(re.fullmatch(schema["pattern"], value), value)
            for value in ("Standard_B2s", "invalid", "B_Standard_"):
                self.assertIsNone(re.fullmatch(schema["pattern"], value), value)
        self.assertEqual(*fields)

    def test_team_examples(self):
        for directory, kind in CASES.items():
            with self.subTest(kind=kind):
                folder = APIS / "azure" / directory
                fixtures = TESTS / "azure" / directory
                xr = next(doc for doc in EXAMPLES if doc.get("kind") == kind)
                xr["metadata"]["namespace"] = "crd-dev"
                xr["spec"]["location"] = "swedencentral"
                xrd = yaml.safe_load((folder / "definition.yaml").read_text())
                properties = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
                for name, schema in properties.items():
                    if "default" in schema:
                        xr["spec"].setdefault(name, schema["default"])
                composition = yaml.safe_load((folder / "composition.yaml").read_text())
                template = composition["spec"]["pipeline"][0]["input"]["inline"]["template"]
                result = subprocess.run(
                    ["go", "run", str(RENDER)],
                    input=json.dumps({"template": template, "xr": xr}),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = list(yaml.safe_load_all(result.stdout))
                expected = list(yaml.safe_load_all((fixtures / "expected.yaml").read_text()))
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
                    alternate["spec"].pop("skuName", None)
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
                    ["go", "run", str(RENDER)],
                    input=json.dumps({"template": template, "xr": alternate}),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(alternate_result.returncode, 0, alternate_result.stderr)
                self.assertEqual(list(yaml.safe_load_all(alternate_result.stdout)), alternate_expected)

if __name__ == "__main__":
    unittest.main()
