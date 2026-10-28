"""Build requests for the real, pinned kro function, without Docker or a cluster.

Usage: python3 .../build_runtime_requests.py <existing-project-relative-output-dir>
Only generated request/response files go in that directory.
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
APIS = ROOT.parents[1]
AZURE = APIS / "azure"


def load(path):
    return yaml.safe_load(path.read_text())


def augmented(schema):
    schema = copy.deepcopy(schema)
    schema.setdefault("properties", {}).update({
        "apiVersion": {"type": "string"}, "kind": {"type": "string"},
        "metadata": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "namespace": {"type": "string"},
                "uid": {"type": "string"},
                "annotations": {"type": "object", "additionalProperties": {"type": "string"}},
                "labels": {"type": "object", "additionalProperties": {"type": "string"}},
            }
        }
    })
    schema["properties"].setdefault("status", {"type": "object", "properties": {}})[
        "properties"
    ]["conditions"] = {
        "type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string"}, "status": {"type": "string"},
        }}
    }
    return schema


def schemas():
    result = {}
    for folder in [
        AZURE / "resourcegroup", AZURE / "subscription",
        AZURE / "log-analytics-workspace", AZURE / "user-assigned-identity",
        AZURE / "data-collection-rule", AZURE / "diagnostic-setting", ROOT,
    ]:
        xrd = load(folder / "definition.yaml")
        version = xrd["spec"]["versions"][0]
        key = xrd["spec"]["group"] + "/" + version["name"] + ", Kind=" + xrd[
            "spec"
        ]["names"]["kind"]
        result[key] = {"openapiV3": augmented(version["schema"]["openAPIV3Schema"])}
    for fixture in [
        AZURE / "data-collection-rule/tests/provider-schema.json",
        ROOT / "tests/provider-config-schema.json",
    ]:
        crd = json.loads(fixture.read_text())["crd"]
        version = next(v for v in crd["spec"]["versions"] if v["storage"])
        key = crd["spec"]["group"] + "/" + version["name"] + ", Kind=" + crd[
            "spec"
        ]["names"]["kind"]
        result[key] = {"openapiV3": augmented(version["schema"]["openAPIV3Schema"])}
    return result


def request(xr, graph, observed=None, required=None):
    return {
        "meta": {"tag": "management-fixture", "capabilities": [
            "CAPABILITY_CAPABILITIES", "CAPABILITY_REQUIRED_SCHEMAS"
        ]},
        "input": graph,
        "observed": {"composite": {"resource": xr}, "resources": observed or {}},
        "desired": {"composite": {"resource": xr}, "resources": {}},
        "requiredSchemas": schemas(),
        "requiredResources": required or {},
    }


def build_cases():
    cases = []
    dcr = load(AZURE / "data-collection-rule/composition.yaml")["spec"]["pipeline"][0][
        "input"
    ]
    for profile in ["vmInsights", "changeTracking", "defenderSql"]:
        xr = {
            "apiVersion": "azure.platform.example.org/v1alpha1",
            "kind": "XDataCollectionRule",
            "metadata": {"name": "operations-" + profile.lower(), "namespace": "platform"},
            "spec": {
                "name": "operations-" + profile.lower(), "profile": profile,
                "resourceGroupName": "rg-operations-monitoring", "location": "swedencentral",
                "workspaceId": "/subscriptions/test/resourceGroups/rg/providers/"
                               "Microsoft.OperationalInsights/workspaces/law",
                "identityId": "/subscriptions/test/resourceGroups/rg/providers/"
                              "Microsoft.ManagedIdentity/userAssignedIdentities/ama",
                "providerConfigRef": {"kind": "ProviderConfig", "name": "operations-management"},
            },
        }
        cases.append({
            "name": profile, "request": request(xr, dcr), "wantCount": 1,
        })
    spec = importlib.util.spec_from_file_location("management_contract", ROOT / "tests/test_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    test = module.ManagementContracts()
    test.setUp()
    xr = test.xr
    observed = test.full_observed()
    for node, resource in observed.items():
        resource["resource"].setdefault("spec", {})
        resource["resource"]["metadata"]["namespace"] = "platform"
    observed["resourceGroup"]["resource"]["status"]["name"] = "rg-operations-monitoring"
    observed["subscription"]["resource"]["status"]["associationId"] = "/association/observed"
    observed["subscription"]["resource"]["spec"]["managementGroupId"] = xr["spec"]["managementGroupId"]
    observed["identity"]["resource"]["status"].update({"clientId": "client", "principalId": "principal"})
    target = observed["managementProviderConfig"]["resource"]
    target["metadata"]["annotations"] = {
        "management.platform.example.org/group-id": xr["spec"]["managementGroupId"]
    }
    graph = load(ROOT / "composition.yaml")["spec"]["pipeline"][1]["input"]
    cases.append({
        "name": "managementObserved",
        "request": request(xr, graph, observed, {"targetProvider": {"items": [{"resource": target}]}}),
        "wantCount": 8,
    })
    cases.append({
        "name": "managementPendingProvider", "request": request(xr, graph),
        "wantCount": 0,
    })
    return cases


def main(output):
    output = output.resolve()
    if not output.is_relative_to(Path.cwd().resolve()):
        raise ValueError("output must be inside the current project")
    (output / "kro-requests.json").write_text(json.dumps(build_cases(), indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
