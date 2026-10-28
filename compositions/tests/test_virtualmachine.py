import copy
import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apis/azure/virtualmachine"


class VirtualMachineReadinessTests(unittest.TestCase):
    def setUp(self):
        self.composition = yaml.safe_load((API / "composition.yaml").read_text())
        self.template = self.composition["spec"]["pipeline"][1]["input"]["inline"][
            "template"
        ]
        self.xr = {
            "spec": {
                "os": "linux",
                "virtualNetworkRef": {"name": "demo", "subnet": "workload"},
                "sshPublicKeySecretRef": {"name": "demo-ssh", "key": "id_ed25519.pub"},
            },
            "metadata": {"namespace": "demo"},
        }

    def render(self, observed, desired):
        result = subprocess.run(
            ["go", "run", str(ROOT / "tests/render.go")],
            input=json.dumps({
                "template": self.template, "xr": self.xr,
                "observed": observed, "desired": desired,
            }),
            capture_output=True, text=True, check=True,
        )
        return list(yaml.safe_load_all(result.stdout))

    def test_pipeline_and_resource_keys_are_preserved(self):
        pipeline = self.composition["spec"]["pipeline"]
        self.assertEqual([step["step"] for step in pipeline], [
            "kro", "check-virtual-machine", "auto-ready",
        ])
        self.assertEqual(
            [r["id"] for r in pipeline[0]["input"]["resources"]],
            ["virtualNetwork", "sshPublicKey", "networkInterface", "linuxVm", "windowsVm"],
        )

    def test_selected_vm_must_be_desired_observed_ready_and_have_id(self):
        for os, key, other in (
            ("linux", "linuxVm", "windowsVm"),
            ("windows", "windowsVm", "linuxVm"),
        ):
            self.xr["spec"]["os"] = os
            ready_vm = {
                "resource": {
                    "status": {
                        "atProvider": {"id": "/vm/selected"},
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                },
            }
            for case in (
                "nic-only", "wrong-os", "missing-observed", "pending",
                "unknown", "no-condition", "no-id", "empty-id", "ready", "omitted",
            ):
                with self.subTest(os=os, case=case):
                    observed = {
                        "networkInterface": {"resource": {
                            "status": {"atProvider": {"id": "/nic/demo"},
                                       "conditions": [{"type": "Ready", "status": "True"}]},
                        }},
                    }
                    desired = {"networkInterface": {"resource": {}}}
                    if case == "wrong-os":
                        observed[other] = ready_vm
                        desired[other] = ready_vm
                    elif case != "nic-only":
                        desired[key] = {"resource": {}}
                        observed[key] = copy.deepcopy(ready_vm)
                        status = observed[key]["resource"]["status"]
                        if case == "missing-observed":
                            del observed[key]
                        elif case in ("pending", "unknown"):
                            status["conditions"][0]["status"] = (
                                "False" if case == "pending" else "Unknown"
                            )
                        elif case == "no-condition":
                            status.pop("conditions")
                        elif case == "no-id":
                            status.pop("atProvider")
                        elif case == "empty-id":
                            status["atProvider"]["id"] = ""
                        elif case == "omitted":
                            del desired[key]
                    xr, conditions = self.render(observed, desired)
                    condition = conditions["conditions"][0]
                    self.assertEqual(condition["type"], "VirtualMachineReady")
                    self.assertEqual(condition["status"], "True" if case == "ready" else "False")
                    annotations = xr["metadata"]["annotations"] or {}
                    if case == "ready":
                        self.assertNotIn("gotemplating.fn.crossplane.io/ready", annotations)
                    else:
                        self.assertEqual(
                            annotations["gotemplating.fn.crossplane.io/ready"], "False",
                        )
                    expected_id = "/vm/selected" if key in observed and case not in (
                        "no-id", "empty-id",
                    ) else ""
                    self.assertEqual(xr["status"]["id"], expected_id)
                    if case in ("nic-only", "wrong-os", "omitted"):
                        self.assertEqual(condition["reason"], "WaitingForDependencies")
                        self.assertIn("XVirtualNetwork demo subnet workload", condition["message"])
                        self.assertEqual("Secret demo-ssh" in condition["message"], os == "linux")


if __name__ == "__main__":
    unittest.main()
