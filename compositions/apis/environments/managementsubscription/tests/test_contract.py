import copy
import graphlib
import json
import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
APIS = ROOT.parents[1]
AZURE = APIS / "azure"
PRIMITIVES = {
    "subscription": "ManagementGroupSubscriptionAssociation",
    "log-analytics-workspace": "Workspace",
    "user-assigned-identity": "UserAssignedIdentity",
    "data-collection-rule": "MonitorDataCollectionRule",
    "diagnostic-setting": "MonitorDiagnosticSetting",
}


def load(path):
    return yaml.safe_load(path.read_text())


def render(template, xr, observed=None, extra=None, succeeds=True):
    result = subprocess.run(
        ["go", "run", str(ROOT / "tests/render.go")],
        input=json.dumps(
            {"template": template, "xr": xr, "observed": observed or {}, "extra": extra}
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    if not succeeds:
        if result.returncode == 0:
            raise AssertionError("template unexpectedly accepted invalid/pending input")
        return result.stderr
    if result.returncode:
        raise AssertionError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def resource_map(documents):
    return {
        d["metadata"]["annotations"][
            "gotemplating.fn.crossplane.io/composition-resource-name"
        ]: d
        for d in documents
        if "metadata" in d
    }


class PrimitiveContracts(unittest.TestCase):
    def test_exact_pinned_namespaced_provider_contracts(self):
        for folder, kind in PRIMITIVES.items():
            with self.subTest(folder=folder):
                fixture = json.loads(
                    (AZURE / folder / "tests/provider-schema.json").read_text()
                )
                self.assertTrue(fixture["package"].endswith(":v2.7.2"))
                crd = fixture["crd"]
                self.assertEqual(crd["spec"]["scope"], "Namespaced")
                self.assertEqual(crd["spec"]["names"]["kind"], kind)
                self.assertRegex(fixture["extractedPackageSHA256"], r"^[0-9a-f]{64}$")
                xrd = load(AZURE / folder / "definition.yaml")
                self.assertEqual(xrd["spec"]["scope"], "Namespaced")
                fields = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
                    "properties"
                ]
                self.assertIn("id", fields["status"]["properties"])
                self.assertIn("managedResourceName", fields["status"]["properties"])
                self.assertEqual(
                    fields["spec"]["properties"]["providerConfigRef"]["properties"][
                        "name"
                    ]["default"],
                    "default",
                )

    def test_patch_paths_match_exact_provider_fields(self):
        for folder in set(PRIMITIVES) - {"data-collection-rule"}:
            fixture = json.loads(
                (AZURE / folder / "tests/provider-schema.json").read_text()
            )
            crd = fixture["crd"]
            schema = next(v for v in crd["spec"]["versions"] if v["storage"])[
                "schema"
            ]["openAPIV3Schema"]
            composition = load(AZURE / folder / "composition.yaml")
            resources = composition["spec"]["pipeline"][0]["input"]["resources"]
            self.assertEqual(len(resources), 1)
            resource = resources[0]
            self.assertEqual(resource["base"]["kind"], PRIMITIVES[folder])
            self.assertEqual(
                resource["base"]["apiVersion"], crd["spec"]["group"] + "/v1beta1"
            )
            for patch in resource["patches"]:
                path = patch[
                    "fromFieldPath"
                    if patch["type"] == "ToCompositeFieldPath"
                    else "toFieldPath"
                ]
                if path.startswith("metadata."):
                    continue
                node = schema
                for segment in path.split("."):
                    self.assertIn(segment, node["properties"], (folder, path))
                    node = node["properties"][segment]

    def test_existing_subscription_has_only_association_and_observed_outputs(self):
        composition = load(AZURE / "subscription/composition.yaml")
        resources = composition["spec"]["pipeline"][0]["input"]["resources"]
        self.assertEqual([r["base"]["kind"] for r in resources], [
            "ManagementGroupSubscriptionAssociation"
        ])
        outputs = {
            p["toFieldPath"]: p for p in resources[0]["patches"]
            if p["type"] == "ToCompositeFieldPath"
        }
        self.assertEqual(
            outputs["status.id"]["fromFieldPath"], "status.atProvider.subscriptionId"
        )
        self.assertEqual(
            outputs["status.associationId"]["fromFieldPath"], "status.atProvider.id"
        )
        self.assertEqual(
            outputs["status.id"]["transforms"][0]["string"]["fmt"], "/subscriptions/%s"
        )

    def test_resource_group_default_and_provider_override_are_compatible(self):
        resource = load(AZURE / "resourcegroup/composition.yaml")["spec"]["pipeline"][
            0
        ]["input"]["resources"][0]
        self.assertEqual(
            resource["base"]["spec"]["providerConfigRef"],
            {"kind": "ClusterProviderConfig", "name": "default"},
        )
        self.assertIn(
            {"type": "FromCompositeFieldPath", "fromFieldPath": "spec.providerConfigRef",
             "toFieldPath": "spec.providerConfigRef"},
            resource["patches"],
        )

    def test_dcr_profiles_use_exact_object_not_legacy_array_shapes(self):
        composition = load(AZURE / "data-collection-rule/composition.yaml")
        resource = composition["spec"]["pipeline"][0]["input"]["resources"][0]
        provider = resource["template"]["spec"]["forProvider"]
        self.assertIsInstance(provider["destinations"], dict)
        self.assertIsInstance(provider["identity"], dict)
        self.assertIsInstance(provider["dataFlow"], list)
        self.assertIn("performanceCounter", provider["dataSources"])
        self.assertIn("extensionJson", provider["dataSources"])
        self.assertNotIn("enableCollectionOfSqlQueriesForSecurityResearch\\\":true",
                         provider["dataSources"])
        self.assertNotIn("dataCollectionEndpointId", provider)

    def test_activity_log_categories_are_all_subscription_categories(self):
        resource = load(AZURE / "diagnostic-setting/composition.yaml")["spec"][
            "pipeline"
        ][0]["input"]["resources"][0]
        self.assertEqual(
            {log["category"] for log in resource["base"]["spec"]["forProvider"][
                "enabledLog"
            ]},
            {"Administrative", "Security", "ServiceHealth", "Alert",
             "Recommendation", "Policy", "Autoscale", "ResourceHealth"},
        )

    def test_workspace_uses_canonical_auth_flag_and_does_not_publish_keys(self):
        resource = load(AZURE / "log-analytics-workspace/composition.yaml")["spec"][
            "pipeline"
        ][0]["input"]["resources"][0]
        provider = resource["base"]["spec"]["forProvider"]
        self.assertIs(provider["localAuthenticationEnabled"], False)
        self.assertNotIn("localAuthenticationDisabled", provider)
        status = load(AZURE / "log-analytics-workspace/definition.yaml")["spec"][
            "versions"
        ][0]["schema"]["openAPIV3Schema"]["properties"]["status"]["properties"]
        self.assertEqual(set(status), {"id", "workspaceId", "managedResourceName"})


class ManagementContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.composition = load(ROOT / "composition.yaml")
        cls.steps = cls.composition["spec"]["pipeline"]
        cls.preflight = cls.steps[0]["input"]["inline"]["template"]
        cls.lifecycle = cls.steps[-1]["input"]["inline"]["template"]

    def setUp(self):
        self.xr = load(ROOT / "tests/external.yaml")
        self.source = {
            "apiVersion": "azure.m.upbound.io/v1beta1",
            "kind": "ClusterProviderConfig",
            "metadata": {"name": "default"},
            "spec": {
                "subscriptionID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "tenantID": "tenant",
                "environment": "usgovernment",
                "clientID": "client",
                "credentials": {"source": "Secret", "secretRef": {
                    "namespace": "crossplane-system", "name": "azure-secret", "key": "creds"
                }},
                "features": {"postgreSQLFlexibleServer": {
                    "restartServerOnConfigurationValueChange": False
                }},
            },
        }
        self.extra = {"sourceProvider": {"items": [{"resource": self.source}]}}

    def test_source_spec_is_copied_and_only_target_subscription_overridden(self):
        before = copy.deepcopy(self.source)
        resources = resource_map(render(self.preflight, self.xr, extra=self.extra))
        target = resources["managementProviderConfig"]
        expected = copy.deepcopy(self.source["spec"])
        expected["subscriptionID"] = self.xr["spec"]["subscriptionId"]
        self.assertEqual(target["spec"], expected)
        self.assertEqual(target["kind"], "ProviderConfig")
        self.assertEqual(target["metadata"]["namespace"], "platform")
        self.assertEqual(self.source, before)
        self.assertNotIn('"data":', json.dumps(target))
        self.assertNotIn("status", target)

    def test_namespaced_source_and_landing_zone_select_same_namespace(self):
        xr = load(ROOT / "tests/landing-zone.yaml")
        xr["spec"]["providerConfigRef"] = {"kind": "ProviderConfig", "name": "team"}
        zone = {
            "status": {
                "conditions": [{"type": "Ready", "status": "False"}],
                "managementGroups": {"management": {
                    "id": "/providers/Microsoft.Management/managementGroups/observed",
                    "name": "observed", "displayName": "Management"
                }},
            }
        }
        self.extra["landingZone"] = {"items": [{"resource": zone}]}
        rendered = render(self.preflight, xr, extra=self.extra)
        requirements = rendered[0]["requirements"]
        self.assertEqual(requirements["sourceProvider"]["namespace"], "platform")
        self.assertEqual(requirements["landingZone"]["namespace"], "platform")
        resources = resource_map(rendered)
        target = resources["managementProviderConfig"]
        self.assertTrue(target["metadata"]["annotations"][
            "management.platform.example.org/group-id"
        ].endswith("/observed"))
        usage = resources["landingZoneUsage"]
        self.assertEqual(usage["spec"]["by"]["kind"], "ProviderConfig")
        self.assertEqual(usage["spec"]["of"]["kind"], "XLandingZone")
        self.assertNotIn("ownerReferences", usage["spec"]["of"])

    def test_missing_source_and_pending_group_fail_without_pruning(self):
        self.assertIn(
            "refusing fallback",
            render(self.preflight, self.xr, extra={"sourceProvider": {"items": []}},
                   succeeds=False),
        )
        xr = load(ROOT / "tests/landing-zone.yaml")
        self.extra["landingZone"] = {"items": []}
        self.assertIn("landingZoneRef is missing",
                      render(self.preflight, xr, extra=self.extra, succeeds=False))
        self.extra["landingZone"] = {"items": [{"resource": {
            "status": {"managementGroups": {}}
        }}]}
        self.assertIn("observed ID",
                      render(self.preflight, xr, extra=self.extra, succeeds=False))

    def test_sequencer_and_explicit_expected_readiness(self):
        sequence = self.steps[2]["input"]
        self.assertEqual(self.steps[2]["functionRef"]["name"], "function-sequencer")
        for key in ["enableDeletionSequencing", "replayDeletion",
                    "resetCompositeReadiness"]:
            self.assertIs(sequence[key], True)
        self.assertEqual(sequence["usageVersion"], "v2")
        resources = resource_map(render(self.lifecycle, self.xr))
        self.assertEqual(resources["readinessGate"]["metadata"]["annotations"][
            "gotemplating.fn.crossplane.io/ready"
        ], "False")
        self.assertEqual(self.steps[-1]["step"], "nested-lifecycle-and-readiness")

    def full_observed(self):
        observed = {
            "managementProviderConfig": {"resource": {
                "apiVersion": "azure.m.upbound.io/v1beta1", "kind": "ProviderConfig",
                "metadata": {"name": "operations-management"},
                "spec": {"subscriptionID": self.xr["spec"]["subscriptionId"]},
            }}
        }
        kinds = {
            "subscription": "XSubscription", "resourceGroup": "XResourceGroup",
            "workspace": "XLogAnalyticsWorkspace", "identity": "XUserAssignedIdentity",
            "vmInsights": "XDataCollectionRule", "changeTracking": "XDataCollectionRule",
            "defenderSql": "XDataCollectionRule", "diagnosticSetting": "XDiagnosticSetting"
        }
        for node, kind in kinds.items():
            observed[node] = {"resource": {
                "apiVersion": "azure.platform.example.org/v1alpha1", "kind": kind,
                "metadata": {"name": node + "-xr"},
                "status": {
                    "id": "/observed/" + node,
                    "managedResourceName": node + "-actual-mr",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }}
        return observed

    def test_nested_usage_graph_and_provider_lifetime(self):
        observed = self.full_observed()
        resources = resource_map(render(self.lifecycle, self.xr, observed=observed))
        usages = {k: v for k, v in resources.items() if v["kind"] == "Usage"}
        self.assertEqual(len(usages), 22)
        for usage in usages.values():
            self.assertTrue(usage["spec"]["replayDeletion"])
            self.assertTrue(usage["spec"]["by"]["resourceRef"]["name"].endswith(
                "-actual-mr"
            ))
            self.assertFalse(usage["spec"]["by"]["kind"].startswith("X"))
        for node in observed.keys() - {"managementProviderConfig"}:
            usage = usages["nested-" + node + "-managementProviderConfig"]
            self.assertEqual(usage["spec"]["of"]["resourceRef"]["name"],
                             "operations-management")
        self.assertEqual(
            usages["nested-resourceGroup-subscription"]["spec"]["by"]["kind"],
            "ResourceGroup",
        )
        for node in ["vmInsights", "changeTracking", "defenderSql", "diagnosticSetting"]:
            self.assertIn("nested-" + node + "-workspace", usages)
        self.assertEqual(resources["readinessGate"]["data"]["complete"], "false")
        for name, usage in usages.items():
            usage["status"] = {"conditions": [{"type": "Ready", "status": "True"}]}
            observed[name] = {"resource": usage}
        complete = resource_map(render(self.lifecycle, self.xr, observed=observed))
        self.assertEqual(complete["readinessGate"]["data"]["complete"], "true")
        observed["managementProviderConfig"]["resource"]["spec"]["subscriptionID"] = "wrong"
        wrong = resource_map(render(self.lifecycle, self.xr, observed=observed))
        self.assertEqual(wrong["readinessGate"]["data"]["complete"], "false")

    def test_nested_cleanup_graph_is_acyclic_even_when_xrs_disappear_early(self):
        observed = self.full_observed()
        resources = resource_map(render(self.lifecycle, self.xr, observed=observed))
        # A child XR can disappear before its MR finishes deleting. Treat
        # those as distinct graph nodes instead of collapsing their lifetimes.
        predecessors = {}
        for value in observed.values():
            resource = value["resource"]
            status = resource.get("status", {})
            if "managedResourceName" in status:
                predecessors[status["managedResourceName"]] = {
                    resource["metadata"]["name"]
                }
        for resource in resources.values():
            if resource["kind"] != "Usage":
                continue
            using = resource["spec"]["by"]["resourceRef"]["name"]
            used = resource["spec"]["of"]["resourceRef"]["name"]
            predecessors.setdefault(used, set()).add(using)
        order = list(graphlib.TopologicalSorter(predecessors).static_order())
        self.assertEqual(order[-1], "operations-management")
        for node in ["vmInsights", "changeTracking", "defenderSql", "diagnosticSetting"]:
            self.assertLess(order.index(node + "-actual-mr"),
                            order.index("workspace-xr"))
        self.assertLess(order.index("workspace-actual-mr"),
                        order.index("resourceGroup-xr"))
        self.assertLess(order.index("resourceGroup-actual-mr"),
                        order.index("subscription-xr"))
        self.assertLess(order.index("subscription-actual-mr"),
                        order.index("operations-management"))

    def test_bridge_persists_if_owning_child_xr_disappears_first(self):
        observed = self.full_observed()
        resources = resource_map(render(self.lifecycle, self.xr, observed=observed))
        name = "nested-workspace-managementProviderConfig"
        observed[name] = {"resource": resources[name]}
        del observed["workspace"]
        remaining = resource_map(render(self.lifecycle, self.xr, observed=observed))
        self.assertEqual(remaining[name]["spec"], resources[name]["spec"])
        self.assertEqual(remaining["readinessGate"]["data"]["complete"], "false")

    def test_existing_children_are_preserved_while_provider_lookup_is_pending(self):
        observed = self.full_observed()
        for value in observed.values():
            value["resource"].setdefault("spec", {})
        rendered = resource_map(render(self.preflight, self.xr, observed=observed))
        self.assertEqual(
            set(rendered),
            {"subscription", "resourceGroup", "workspace", "identity", "vmInsights",
             "changeTracking", "defenderSql", "diagnosticSetting"},
        )
        self.assertTrue(all(
            child["metadata"]["annotations"]["gotemplating.fn.crossplane.io/ready"]
            == "False" for child in rendered.values()
        ))

    def test_schema_reference_xor_and_immutable_existing_subscription(self):
        schema = load(ROOT / "definition.yaml")["spec"]["versions"][0]["schema"][
            "openAPIV3Schema"
        ]["properties"]["spec"]
        self.assertIn(
            "has(self.managementGroupId) != has(self.landingZoneRef)",
            [v["rule"] for v in schema["x-kubernetes-validations"]],
        )
        self.assertNotIn("default", schema["properties"]["managementGroupRef"])
        subscription = schema["properties"]["subscriptionId"]
        self.assertTrue(re.fullmatch(
            subscription["pattern"], self.xr["spec"]["subscriptionId"]
        ))
        self.assertIn("self == oldSelf",
                      [v["rule"] for v in subscription["x-kubernetes-validations"]])


if __name__ == "__main__":
    unittest.main()
