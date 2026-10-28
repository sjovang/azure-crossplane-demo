"""Opt-in pinned-function tests; only reads CRD schemas from KUBE_CONTEXT."""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apis/azure/virtualmachine"


@unittest.skipUnless(
    os.environ.get("RUN_VIRTUALMACHINE_RENDER") == "1",
    "Set RUN_VIRTUALMACHINE_RENDER=1 and KUBE_CONTEXT; requires Docker, crossplane, kubectl.",
)
class VirtualMachineRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = tempfile.TemporaryDirectory(prefix=".vm-render-", dir=ROOT / "tests")
        cls.addClassCleanup(work.cleanup)
        cls.work = Path(work.name)
        definitions = []
        for api in ("virtualmachine", "virtualnetwork"):
            xrd = yaml.safe_load(
                (ROOT / f"apis/azure/{api}/definition.yaml").read_text()
            )
            definitions.append((
                xrd["spec"]["names"]["kind"],
                "azure.platform.example.org/v1alpha1",
                xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"],
            ))
        for plural, group, kind in (
            ("networkinterfaces", "network", "NetworkInterface"),
            ("linuxvirtualmachines", "compute", "LinuxVirtualMachine"),
            ("windowsvirtualmachines", "compute", "WindowsVirtualMachine"),
        ):
            api_group = f"{group}.azure.m.upbound.io"
            crd = yaml.safe_load(subprocess.check_output([
                "kubectl", "--context", os.environ["KUBE_CONTEXT"],
                "get", "crd", f"{plural}.{api_group}", "-o", "yaml",
            ], text=True))
            if crd["spec"]["scope"] != "Namespaced" or crd["spec"]["names"]["kind"] != kind:
                raise AssertionError(f"Unexpected scope or kind for {plural}")
            schema = next(
                v["schema"]["openAPIV3Schema"]
                for v in crd["spec"]["versions"] if v["name"] == "v1beta1"
            )
            definitions.append((kind, f"{api_group}/v1beta1", schema))
        definitions.append(("Secret", "v1", {
            "type": "object",
            "properties": {"data": {
                "type": "object", "additionalProperties": {"type": "string"},
            }},
        }))
        components = {}
        for kind, api_version, schema in definitions:
            group, _, version = api_version.rpartition("/")
            schema["properties"].update({
                "apiVersion": {"type": "string"},
                "kind": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        **{field: {"type": "string"} for field in ("name", "namespace", "uid")},
                        "annotations": {
                            "type": "object", "additionalProperties": {"type": "string"},
                        },
                    },
                },
            })
            schema["x-kubernetes-group-version-kind"] = [{
                "group": group, "version": version, "kind": kind,
            }]
            components[kind] = schema
        schemas = cls.work / "schemas"
        schemas.mkdir()
        (schemas / "schemas.json").write_text(json.dumps({
            "openapi": "3.0.0", "info": {"title": "VM test schemas", "version": "1"},
            "paths": {}, "components": {"schemas": components},
        }))
        cls.write("functions.yaml", [
            yaml.safe_load((
                ROOT.parent / f"clusters/base/crossplane/functions/{name}.yaml"
            ).read_text())
            for name in ("kro", "go-templating", "auto-ready")
        ])

    @classmethod
    def write(cls, name, documents):
        path = cls.work / name
        path.write_text(yaml.safe_dump_all(documents, explicit_start=True))
        return path

    def render(self, xr, observed=(), required=(), baseline=False):
        composition = yaml.safe_load((API / "composition.yaml").read_text())
        if baseline:
            composition["spec"]["pipeline"] = [
                step for step in composition["spec"]["pipeline"]
                if step["step"] != "check-virtual-machine"
            ]
        result = subprocess.run([
            "crossplane", "render", str(self.write("xr.yaml", [xr])),
            str(self.write("composition.yaml", [composition])),
            str(self.work / "functions.yaml"),
            "--observed-resources", str(self.write("observed.yaml", observed)),
            "--required-resources", str(self.write("required.yaml", required)),
            "--required-schemas", str(self.work / "schemas"),
            "--crossplane-version", "v2.4.0",
            "--cache-dir", str(self.work / "cache"), "--timeout", "2m",
        ], capture_output=True, text=True, cwd=ROOT.parent, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(yaml.safe_load_all(result.stdout))

    def condition(self, rendered, name):
        return next(c for c in rendered[0]["status"]["conditions"] if c["type"] == name)

    def assert_ready(self, rendered, ready):
        self.assertEqual(self.condition(rendered, "Ready")["status"], str(ready))
        self.assertEqual(
            self.condition(rendered, "VirtualMachineReady")["status"], str(ready),
        )

    def test_linux_and_windows_dependency_and_vm_lifecycle(self):
        network = {
            "apiVersion": "azure.platform.example.org/v1alpha1",
            "kind": "XVirtualNetwork",
            "metadata": {"name": "demo", "namespace": "demo"},
            "status": {"subnets": [{"name": "workload", "id": "/subnet/demo"}]},
        }
        # Synthetic public data only; no live Secret is read or changed.
        secret = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": "demo-ssh", "namespace": "demo"},
            "data": {"pub": "c3NoLWVkMjU1MTkgdGVzdC1wdWJsaWMta2V5"},
        }
        for os_name, key, kind in (
            ("linux", "linuxVm", "LinuxVirtualMachine"),
            ("windows", "windowsVm", "WindowsVirtualMachine"),
        ):
            with self.subTest(os=os_name):
                xr = {
                    "apiVersion": "azure.platform.example.org/v1alpha1",
                    "kind": "XVirtualMachine",
                    "metadata": {
                        "name": "demo", "namespace": "demo",
                        "uid": "11111111-1111-4111-8111-111111111111",
                    },
                    "spec": {
                        "name": "demo", "os": os_name, "size": "small",
                        "location": "swedencentral", "adminUsername": "azureuser",
                        "resourceGroupRef": {"name": "demo"},
                        "virtualNetworkRef": {"name": "demo", "subnet": "workload"},
                        **({
                            "sshPublicKeySecretRef": {
                                "name": "demo-ssh", "key": "pub",
                            },
                        } if os_name == "linux" else {
                            "adminPasswordSecretRef": {"name": "demo-password", "key": "password"},
                        }),
                    },
                }
                required = [network, secret] if os_name == "linux" else [network]
                missing_network = self.render(xr, required=[secret] if os_name == "linux" else [])
                self.assert_ready(missing_network, False)
                self.assertEqual(len(missing_network), 1)

                initial = self.render(xr, required=required)
                self.assert_ready(initial, False)
                nic = next(r for r in initial if r["kind"] == "NetworkInterface")
                nic["status"] = {
                    "atProvider": {"id": "/nic/demo", "privateIpAddress": "10.0.0.4"},
                    "conditions": [{"type": "Ready", "status": "True"}],
                }
                if os_name == "linux":
                    baseline = self.render(xr, [nic], [network], baseline=True)
                    self.assertEqual(self.condition(baseline, "Ready")["status"], "True")
                    self.assertEqual([r["kind"] for r in baseline[1:]], ["NetworkInterface"])
                    missing_secret = self.render(xr, [nic], [network])
                    self.assert_ready(missing_secret, False)
                    self.assertEqual(
                        self.condition(missing_secret, "VirtualMachineReady")["reason"],
                        "WaitingForDependencies",
                    )
                    self.assertIn("Secret demo-ssh key pub", str(missing_secret))
                    self.assertEqual(missing_secret[0]["status"]["id"], "")
                    wrong_key = copy.deepcopy(secret)
                    wrong_key["data"] = {}
                    self.assert_ready(self.render(xr, [nic], [network, wrong_key]), False)

                emitted = self.render(xr, [nic], required)
                self.assert_ready(emitted, False)
                vm = next(r for r in emitted if r["kind"] == kind)
                self.assertEqual(
                    vm["metadata"]["annotations"]["crossplane.io/composition-resource-name"], key,
                )
                self.assertEqual(vm["metadata"]["ownerReferences"][0]["kind"], "XVirtualMachine")
                self.assertEqual(vm["spec"]["forProvider"]["networkInterfaceIds"], ["/nic/demo"])
                self.assertEqual(vm["spec"]["forProvider"]["size"], "Standard_B2s")
                if os_name == "windows":
                    self.assertEqual(vm["spec"]["forProvider"]["adminPasswordSecretRef"], {
                        "name": "demo-password", "key": "password",
                    })
                vm["status"] = {
                    "atProvider": {"id": "/vm/demo"},
                    "conditions": [{"type": "Ready", "status": "False"}],
                }
                pending = self.render(xr, [nic, vm], required)
                self.assert_ready(pending, False)
                self.assertEqual(pending[0]["status"]["id"], "/vm/demo")
                vm["status"]["conditions"][0]["status"] = "True"
                ready = self.render(xr, [nic, vm], required)
                self.assert_ready(ready, True)
                self.assertEqual(ready[0]["status"]["id"], "/vm/demo")
                self.assertEqual(ready[0]["status"]["privateIp"], "10.0.0.4")
                pending_nic = copy.deepcopy(nic)
                pending_nic["status"]["conditions"][0]["status"] = "False"
                nic_not_ready = self.render(xr, [pending_nic, vm], required)
                self.assertEqual(self.condition(nic_not_ready, "Ready")["status"], "False")
                self.assertEqual(
                    self.condition(nic_not_ready, "VirtualMachineReady")["status"], "True",
                )
                xr["status"] = ready[0]["status"]
                vm["status"]["atProvider"].pop("id")
                no_id = self.render(xr, [nic, vm], required)
                self.assert_ready(no_id, False)
                self.assertEqual(no_id[0]["status"]["id"], "")
                if os_name == "linux":
                    lost_dependency = self.render(xr, [nic, vm], [network])
                    self.assert_ready(lost_dependency, False)
                    self.assertEqual(
                        self.condition(lost_dependency, "VirtualMachineReady")["reason"],
                        "WaitingForDependencies",
                    )


if __name__ == "__main__":
    unittest.main()
