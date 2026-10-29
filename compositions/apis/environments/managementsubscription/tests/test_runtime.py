"""Opt-in real-function integration: no cluster access and no Azure writes."""
import copy
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CENTRAL_TESTS = ROOT.parents[2] / "tests"
sys.path.insert(0, str(CENTRAL_TESTS))


def source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    os.environ.get("MANAGEMENT_FUNCTION_ENDPOINTS"),
    "set MANAGEMENT_FUNCTION_ENDPOINTS and use the Crossplane Python SDK",
)
class RealManagementFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from function_client import run_pipeline

        cls.run_pipeline = staticmethod(run_pipeline)
        cls.endpoints = json.loads(os.environ["MANAGEMENT_FUNCTION_ENDPOINTS"])
        cls.cases = source(
            "management_runtime_requests", ROOT / "tests/build_runtime_requests.py"
        ).build_cases()
        cls.composition = yaml.safe_load((ROOT / "composition.yaml").read_text())

    def run_case(self, case):
        request = case["request"]
        composition = {"spec": {"pipeline": [{
            "step": case["name"], "functionRef": {"name": "function-kro"},
            "input": request["input"],
        }]}}
        return self.run_pipeline(
            composition, request["observed"]["composite"]["resource"],
            request["requiredSchemas"], self.endpoints,
            observed=request["observed"]["resources"],
            required=request["requiredResources"],
        )

    def test_each_kro_profile_and_observed_management_graph(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                result = self.run_case(case)
                self.assertEqual(len(result.get("resources", {})), case["wantCount"])
                if case["name"] in ["vmInsights", "changeTracking", "defenderSql"]:
                    mr = result["resources"]["dataCollectionRule"]["resource"]
                    self.assertEqual(mr["kind"], "MonitorDataCollectionRule")
                    provider = mr["spec"]["forProvider"]
                    self.assertIsInstance(provider["dataSources"], dict)
                    self.assertIsInstance(provider["destinations"], dict)
                    self.assertEqual(
                        mr["spec"]["providerConfigRef"],
                        {"kind": "ProviderConfig", "name": "operations-management"},
                    )

    def full_request(self):
        request = copy.deepcopy(self.cases[-2]["request"])
        source_provider = {
            "apiVersion": "azure.m.upbound.io/v1beta1", "kind": "ClusterProviderConfig",
            "metadata": {"name": "default"},
            "spec": {
                "subscriptionID": "bootstrap", "tenantID": "tenant",
                "environment": "usgovernment",
                "credentials": {"source": "Secret", "secretRef": {
                    "name": "azure-secret", "namespace": "platform", "key": "creds",
                }},
            },
        }
        request["requiredResources"]["sourceProvider"] = {
            "items": [{"resource": source_provider}]
        }
        return request

    def full_pipeline(self, request):
        return self.run_pipeline(
            self.composition, request["observed"]["composite"]["resource"],
            request["requiredSchemas"], self.endpoints,
            observed=request["observed"]["resources"],
            required=request["requiredResources"],
        )

    def test_full_pipeline_records_nested_edges_before_becoming_ready(self):
        request = self.full_request()
        result = self.full_pipeline(request)
        resources = result["resources"]
        self.assertEqual(resources["readinessGate"]["ready"], "READY_FALSE")
        bridges = {k: v for k, v in resources.items() if k.startswith("nested-")}
        self.assertEqual(len(bridges), 22)
        for key, resource in resources.items():
            if resource["resource"]["kind"] != "Usage":
                continue
            observed = copy.deepcopy(resource)
            observed["resource"].setdefault("metadata", {})["name"] = "mock-" + key.lower()
            observed["resource"]["metadata"]["namespace"] = "platform"
            observed["resource"]["status"] = {"conditions": [
                {"type": "Ready", "status": "True"}
            ]}
            request["observed"]["resources"][key] = observed
        ready = self.full_pipeline(request)
        self.assertEqual(ready["resources"]["readinessGate"]["ready"], "READY_TRUE")
        self.assertTrue(all(
            resource.get("ready") == "READY_TRUE"
            for resource in ready["resources"].values()
        ))
        self.assertEqual(ready["resources"]["managementProviderConfig"]["resource"][
            "spec"
        ]["subscriptionID"], request["observed"]["composite"]["resource"]["spec"][
            "subscriptionId"
        ])

    def test_landing_zone_root_outputs_work_before_landing_zone_ready(self):
        request = self.full_request()
        xr = request["observed"]["composite"]["resource"]
        del xr["spec"]["managementGroupId"]
        xr["spec"]["landingZoneRef"] = {"name": "enterprise"}
        request["requiredResources"]["landingZone"] = {"items": [{"resource": {
            "apiVersion": "azure.platform.example.org/v1alpha1", "kind": "XLandingZone",
            "metadata": {"name": "enterprise", "namespace": "platform"},
            "status": {
                "conditions": [{"type": "Ready", "status": "False"}],
                "managementGroups": {"management": {
                    "id": "/providers/Microsoft.Management/managementGroups/observed",
                    "name": "observed", "displayName": "Management"
                }},
            },
        }}]}
        result = self.full_pipeline(request)
        usage = result["resources"]["landingZoneUsage"]["resource"]
        self.assertEqual(usage["spec"]["of"]["resourceRef"]["name"], "enterprise")
        self.assertEqual(usage["spec"]["by"]["kind"], "ProviderConfig")
        self.assertEqual(result["resources"]["managementProviderConfig"]["resource"][
            "metadata"
        ]["annotations"]["management.platform.example.org/group-id"],
                         "/providers/Microsoft.Management/managementGroups/observed")

    def test_missing_source_is_a_fatal_not_a_partial_desired_set(self):
        request = self.full_request()
        request["requiredResources"]["sourceProvider"] = {"items": []}
        with self.assertRaisesRegex(AssertionError, "refusing fallback or pruning"):
            self.full_pipeline(request)

    def test_cross_namespace_secret_reference_fails_before_target_provider(self):
        request = self.full_request()
        source_provider = request["requiredResources"]["sourceProvider"]["items"][0][
            "resource"
        ]
        source_provider["spec"]["credentials"]["secretRef"]["namespace"] = (
            "crossplane-system"
        )
        with self.assertRaisesRegex(AssertionError, "Secret namespace"):
            self.full_pipeline(request)

    def test_namespaced_source_accepts_local_secret_without_namespace(self):
        request = self.full_request()
        source_provider = request["requiredResources"]["sourceProvider"]["items"][0][
            "resource"
        ]
        source_provider["kind"] = "ProviderConfig"
        source_provider["metadata"]["namespace"] = "platform"
        request["observed"]["composite"]["resource"]["spec"]["providerConfigRef"] = {
            "kind": "ProviderConfig", "name": "default"
        }
        del source_provider["spec"]["credentials"]["secretRef"]["namespace"]
        result = self.full_pipeline(request)
        self.assertEqual(
            result["resources"]["managementProviderConfig"]["resource"]["spec"][
                "credentials"
            ],
            source_provider["spec"]["credentials"],
        )

    def test_namespace_independent_authentication_accepts_cluster_source(self):
        request = self.full_request()
        source_provider = request["requiredResources"]["sourceProvider"]["items"][0][
            "resource"
        ]
        source_provider["spec"]["credentials"] = {
            "source": "SystemAssignedManagedIdentity"
        }
        result = self.full_pipeline(request)
        self.assertEqual(
            result["resources"]["managementProviderConfig"]["resource"]["spec"][
                "credentials"
            ],
            source_provider["spec"]["credentials"],
        )

    def test_missing_target_provider_preserves_existing_children_not_ready(self):
        request = self.full_request()
        observed = request["observed"]["resources"]
        graph_result = self.run_case(self.cases[-2])
        for node, resource in graph_result["resources"].items():
            observed[node]["resource"]["spec"] = resource["resource"]["spec"]
        del observed["managementProviderConfig"]
        request["requiredResources"]["targetProvider"] = {"items": []}
        result = self.full_pipeline(request)
        for node in graph_result["resources"]:
            self.assertIn(node, result["resources"])
            self.assertEqual(
                result["resources"][node]["resource"]["spec"],
                observed[node]["resource"]["spec"],
            )
        self.assertEqual(result["resources"]["readinessGate"]["ready"], "READY_FALSE")
