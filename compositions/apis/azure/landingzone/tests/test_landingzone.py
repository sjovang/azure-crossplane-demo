"""Offline policy projection and schema checks; optional real-function lifecycle."""

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml


API = Path(__file__).resolve().parents[1]
PROJECT = API.parents[2]
spec = importlib.util.spec_from_file_location("alz_generator", API / "generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
sys.path.insert(0, str(PROJECT / "tests"))
from function_client import defaults, observe, run_pipeline, schemas  # noqa: E402


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((API / "library.json").read_text())
        cls.definitions, cls.initiatives, cls.assignments, cls.report = (
            generator.compile_profile(cls.source)
        )
        cls.composition = yaml.safe_load((API / "composition.yaml").read_text())

    def test_locked_generation_is_deterministic(self):
        for name, content in generator.generate().items():
            self.assertEqual((API / name).read_text(), content, name)
        xrd = yaml.safe_load((API / "definition.yaml").read_text())
        self.assertEqual(
            xrd["spec"]["names"]["kind"], "XLandingZoneArchitecture",
        )
        self.assertEqual(
            xrd["metadata"]["name"],
            "xlandingzonearchitectures.azure.platform.example.org",
        )
        self.assertEqual(
            self.composition["spec"]["compositeTypeRef"]["kind"],
            "XLandingZoneArchitecture",
        )
        self.assertLess(len((API / "composition.yaml").read_bytes()), 900000)
        for node in self.composition["spec"]["pipeline"][1]["input"]["resources"]:
            template = node["template"]
            if template["kind"] in ("PolicySetDefinition", "ManagementGroupPolicyAssignment"):
                self.assertLessEqual(len(template["spec"]["forProvider"]["displayName"]), 128)

    def test_every_effect_is_safe_and_every_parameter_valid(self):
        self.assertGreater(len(self.assignments), 50)
        self.assertTrue(any(r["action"] == "audit-alternative" for r in self.report))
        self.assertTrue(any(r["action"] == "omitted" for r in self.report))
        for initiative in self.initiatives.values():
            self.assertTrue(initiative["members"])
            references = [member["referenceId"] for member in initiative["members"]]
            self.assertEqual(len(references), len(set(references)))
            for member in initiative["members"]:
                reference = member["policyDefinitionId"]
                properties = (
                    self.definitions[reference.split(":", 1)[1]]
                    if reference.startswith("@definition:") else
                    self.source["policies"][generator.policy_key(reference)]["properties"]
                )
                effect = generator.resolve(
                    properties["policyRule"]["then"]["effect"],
                    {k: v["value"] for k, v in member["parameters"].items()},
                )
                self.assertIn(effect.lower(), generator.SAFE)
                self.assertEqual(
                    set(member["parameters"]), set(properties.get("parameters", {})),
                )
                for name, value in member["parameters"].items():
                    allowed = properties["parameters"][name].get("allowedValues")
                    if allowed and isinstance(value["value"], str):
                        self.assertIn(value["value"], allowed)

    def test_hardcoded_mutating_effect_is_not_rewritten(self):
        for effect in ("DeployIfNotExists", "Modify", "Append", "DenyAction"):
            result, _, _ = generator.selected_effect({
                "policyRule": {"then": {"effect": effect}},
            }, {})
            self.assertIsNone(result)

    def test_upstream_enforcement_and_supported_compliance_messages_are_preserved(self):
        nodes = {
            node["id"]: node for node in self.composition["spec"]["pipeline"][1]["input"]["resources"]
        }
        for name, assignment in self.assignments.items():
            original = self.source["assignments"][name]["properties"]
            desired = nodes["assignment" + generator.token(name)]["template"]["spec"]["forProvider"]
            self.assertEqual(
                desired["enforce"], original.get("enforcementMode", "Default") == "Default",
            )
            self.assertEqual(
                len(desired.get("nonComplianceMessage", [])),
                len(original.get("nonComplianceMessages", [])),
            )
            self.assertNotIn("{enforcementMode}", json.dumps(desired))

    def test_supported_assignment_effect_overrides_are_preserved(self):
        source = copy.deepcopy(self.source)
        definitions, initiatives, assignments, report = generator.compile_profile(source)
        assignment = assignments["Deny-UnmanagedDisk"]
        self.assertEqual(assignment["overrides"], [{"value": "Deny"}])
        self.assertTrue(any(
            item.get("assignment") == "Deny-UnmanagedDisk"
            and item.get("action") == "assignment-policy-effect-override-retained"
            for item in report
        ))
        node_id = "assignment" + generator.token("Deny-UnmanagedDisk")
        node = next(
            resource for resource in self.composition["spec"]["pipeline"][1]["input"]["resources"]
            if resource["id"] == node_id
        )
        self.assertEqual(
            node["template"]["spec"]["forProvider"]["overrides"], [{"value": "Deny"}],
        )
        self.assertEqual(
            source["assignments"]["Deny-UnmanagedDisk"]["properties"]["overrides"][0]["value"],
            "Deny",
        )
        self.assertIn("safe-", next(iter(initiatives)))
        self.assertTrue(definitions)

    def test_unsupported_assignment_effect_overrides_fail_closed(self):
        for override in (
            {"kind": "unknown", "value": "Deny"},
            {"kind": "policyEffect", "value": "DeployIfNotExists"},
            {"kind": "policyEffect", "value": "Deny", "selectors": []},
        ):
            source = copy.deepcopy(self.source)
            source["assignments"]["Deny-UnmanagedDisk"]["properties"]["overrides"] = [override]
            with self.subTest(override=override), self.assertRaisesRegex(
                ValueError, "unsupported"
            ):
                generator.compile_profile(source)

    def test_unknown_effect_and_expression_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unknown policy effect"):
            generator.selected_effect({"policyRule": {"then": {"effect": "futureEffect"}}}, {})
        with self.assertRaisesRegex(ValueError, "Unsupported parameter expression"):
            generator.resolve("[concat(parameters('unknown'),'x')]", {})

    def test_effect_aliases_and_case_insensitive_arm_parameters(self):
        self.assertEqual(generator.resolve("[parameters('EFFECT')]", {"effect": "Audit"}), "Audit")
        self.assertEqual(generator.resolve(
            "[if(contains(parameters('types'),'x'),parameters('effect'),'Disabled')]",
            {"types": ["y"], "effect": "Deny"},
        ), "Disabled")

    def test_required_inputs_are_typed_and_no_deployment_placeholders(self):
        profile = json.loads((API / "profile.json").read_text())
        patch = yaml.safe_load((API / "xrd-patch.yaml").read_text())
        properties = patch["properties"]["spec"]["properties"]
        self.assertEqual(
            set(profile["requiredInputs"]), set(properties["policyDefaults"]["properties"]),
        )
        self.assertNotIn("placeholder", json.dumps(self.composition))
        self.assertEqual(len(properties["managementGroups"]["default"]), 12)

    def test_structural_schema_defaults_and_bounded_validation(self):
        xrd = yaml.safe_load((API / "definition.yaml").read_text())
        properties = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["properties"]
        validations = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["x-kubernetes-validations"]
        self.assertEqual(properties["providerConfigRef"]["default"], {
            "kind": "ClusterProviderConfig", "name": "default",
        })
        self.assertNotIn("additionalProperties", properties["policyDefaults"])
        self.assertEqual(properties["managementGroups"]["maxItems"], 100)
        self.assertEqual(properties["subscriptions"]["maxItems"], 100)
        archetypes = properties["managementGroups"]["items"]["properties"]["archetypes"]
        self.assertEqual(archetypes["x-kubernetes-list-type"], "set")
        self.assertNotIn("uniqueItems", archetypes)
        self.assertLessEqual(archetypes["maxItems"], 32)
        self.assertEqual(
            properties["subscriptions"]["items"]["properties"]["subscriptionId"]["maxLength"], 36,
        )
        self.assertTrue(any("lowerAscii" in rule["rule"] for rule in validations))

    def test_archetypes_are_optional_without_changing_default_architecture(self):
        xrd = yaml.safe_load((API / "definition.yaml").read_text())
        schema = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
        groups = schema["properties"]["spec"]["properties"]["managementGroups"]
        self.assertNotIn("archetypes", groups["items"]["required"])
        self.assertEqual(groups["items"]["properties"]["archetypes"]["default"], [])
        rules = [item["rule"] for item in groups["x-kubernetes-validations"]]
        self.assertIn(
            "self.all(g, !('root' in g.archetypes) || !has(g.parentRef))", rules,
        )
        self.assertIn("self.filter(g, !has(g.parentRef)).size() == 1", rules)
        expected = []
        for group in self.source["architecture"]["management_groups"]:
            entry = {
                "name": generator.public_name(group["id"]),
                "displayName": group["display_name"],
                "archetypes": [generator.public_name(a) for a in group["archetypes"]],
            }
            if group["parent_id"]:
                entry["parentRef"] = {"name": generator.public_name(group["parent_id"])}
            expected.append(entry)
        self.assertEqual(groups["default"], expected)
        for selection in (None, [], ["sandbox"], ["root"]):
            root = {"name": "root", "displayName": "Root"}
            if selection is not None:
                root["archetypes"] = selection
            xr = defaults({"spec": {"managementGroups": [root]}}, schema)
            self.assertEqual(
                xr["spec"]["managementGroups"][0]["archetypes"],
                [] if selection is None else selection,
            )

    def test_team_example_preserves_topology_without_selecting_policies(self):
        xr = yaml.safe_load(
            (PROJECT.parent / "teams/alz-architecture/landingzone.yaml").read_text()
        )
        xrd = yaml.safe_load((API / "definition.yaml").read_text())
        groups = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["properties"]["managementGroups"]["default"]
        expected = [{**group, "archetypes": []} for group in groups]
        self.assertEqual(xr["spec"]["managementGroups"], expected)
        self.assertFalse(xr["spec"].get("subscriptions"))
        self.assertNotIn("policyDefaults", xr["spec"])
        self.assertNotIn("parentManagementGroupId", xr["spec"])
        self.assertEqual(xr["spec"]["environmentConfigRef"], {"name": "azure-platform"})

    @unittest.skipUnless(shutil.which("crossplane"), "Crossplane CLI schema/CEL validation")
    def test_root_archetype_schema_and_cel_validation(self):
        root = {"name": "root", "displayName": "Root"}
        child = {"name": "child", "displayName": "Child", "parentRef": {"name": "root"}}
        cases = [
            ("omitted", [root, child], None),
            ("empty", [{**root, "archetypes": []}, child], None),
            ("alternate", [{**root, "archetypes": ["sandbox"]}, child], None),
            ("root", [{**root, "archetypes": ["root"]}, child], None),
            ("descendant-root", [root, {**child, "archetypes": ["root"]}],
             "Only the ALZ root may have the root archetype"),
            ("multiple-roots", [root, {"name": "second", "displayName": "Second"}],
             "Exactly one ALZ root group is required"),
            ("unknown-archetype", [{**root, "archetypes": ["unknown"]}],
             "Unsupported value"),
            ("unknown-parent", [root, {**child, "parentRef": {"name": "missing"}}],
             "Every parent reference must identify a group in this list"),
        ]
        for name, groups, error in cases:
            with self.subTest(name=name):
                xr = {
                    "apiVersion": generator.GROUP, "kind": "XLandingZoneArchitecture",
                    "metadata": {"name": "example", "namespace": "alz-test"},
                    "spec": {
                        "name": "example",
                        "parentManagementGroupId":
                            "/providers/Microsoft.Management/managementGroups/external",
                        "managementGroups": groups,
                    },
                }
                result = subprocess.run(
                    ["crossplane", "resource", "validate", str(API / "definition.yaml"),
                     "-", "--error-on-missing-schemas"],
                    input=yaml.safe_dump(xr), text=True, capture_output=True, timeout=60,
                )
                output = result.stdout + result.stderr
                if error is None:
                    self.assertEqual(result.returncode, 0, output)
                else:
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn(error, output)

    @unittest.skipUnless(shutil.which("crossplane"), "Crossplane CLI schema/CEL validation")
    def test_exactly_one_parent_source_is_required(self):
        for direct, reference, valid in (
            (False, False, False), (True, True, False),
            (True, False, True), (False, True, True),
        ):
            with self.subTest(direct=direct, reference=reference):
                xr = {
                    "apiVersion": generator.GROUP, "kind": "XLandingZoneArchitecture",
                    "metadata": {"name": "example", "namespace": "alz-test"},
                    "spec": {"name": "example"},
                }
                if direct:
                    xr["spec"]["parentManagementGroupId"] = (
                        "/providers/Microsoft.Management/managementGroups/external"
                    )
                if reference:
                    xr["spec"]["environmentConfigRef"] = {"name": "azure-platform"}
                result = subprocess.run(
                    ["crossplane", "resource", "validate", str(API / "definition.yaml"),
                     "-", "--error-on-missing-schemas"],
                    input=yaml.safe_dump(xr), text=True, capture_output=True, timeout=60,
                )
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
                if not valid:
                    self.assertIn(
                        "Supply either parentManagementGroupId or environmentConfigRef",
                        result.stdout + result.stderr,
                    )

    def test_foreground_sequencer_and_explicit_dynamic_usages(self):
        pipeline = self.composition["spec"]["pipeline"]
        sequencing = next(s["input"] for s in pipeline if s["step"] == "sequence")
        self.assertTrue(sequencing["enableDeletionSequencing"])
        self.assertEqual(sequencing["usageVersion"], "v2")
        self.assertTrue(sequencing["replayDeletion"])
        self.assertTrue(sequencing["resetCompositeReadiness"])
        for rule in sequencing["rules"]:
            self.assertEqual(len(rule["sequence"]), 2)
        status_template = (API / "status.gotmpl").read_text()
        self.assertIn("protection.crossplane.io/v1beta1", status_template)
        self.assertIn("status | default dict).managedResourceName", status_template)
        self.assertIn("and $ready $associationName", status_template)
        self.assertNotIn('gotemplating.fn.crossplane.io/ready: "True"', status_template)

    def test_attachment_names_satisfy_child_subscription_contract(self):
        xrd = yaml.safe_load((API / "definition.yaml").read_text())
        parent = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["properties"]["subscriptions"]["items"]["properties"]["name"]
        child_xrd = yaml.safe_load(
            (API.parent / "subscription/definition.yaml").read_text()
        )
        child = child_xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["properties"]["name"]
        for name in ("a", "team-prod", "team1-prod2", "a" * 32):
            with self.subTest(name=name):
                self.assertRegex(name, parent["pattern"])
                self.assertRegex(name, child["pattern"])
                self.assertLessEqual(len(name), child["maxLength"])
        for name in ("team--prod", "-team", "team-", "Team", ""):
            with self.subTest(name=name):
                self.assertIsNone(re.fullmatch(parent["pattern"], name))


@unittest.skipUnless(os.environ.get("ALZ_RENDER_SCHEMAS"), "Opt-in pinned-function gRPC tests")
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.composition = yaml.safe_load((API / "composition.yaml").read_text())
        cls.schema = yaml.safe_load((API / "definition.yaml").read_text())[
            "spec"
        ]["versions"][0]["schema"]["openAPIV3Schema"]
        paths = os.environ["ALZ_RENDER_SCHEMAS"].split(os.pathsep)
        cls.schemas = schemas([API / "definition.yaml", *paths])
        subscription = PROJECT / "apis/azure/subscription/definition.yaml"
        if subscription.exists():
            cls.schemas.update(schemas([subscription]))
        cls.endpoints = {
            "function-go-templating": "127.0.0.1:19443",
            "function-kro": "127.0.0.1:19444",
            "function-sequencer": "127.0.0.1:19445",
            "function-auto-ready": "127.0.0.1:19446",
        }

    def xr(self, custom=None):
        value = {
            "apiVersion": generator.GROUP, "kind": "XLandingZoneArchitecture",
            "metadata": {
                "name": "example", "namespace": "alz-test",
                "uid": "11111111-1111-4111-8111-111111111111",
            },
            "spec": {
                "name": "example",
                "parentManagementGroupId": "/providers/Microsoft.Management/managementGroups/external",
            },
        }
        if custom is not None:
            value["spec"]["managementGroups"] = custom
        return defaults(value, self.schema)

    def render(self, xr, observed=None, required=None):
        return run_pipeline(
            self.composition, xr, self.schemas, self.endpoints, observed, required,
        )

    def test_environment_config_resolves_parent_without_owning_config(self):
        xr = self.xr([{"name": "root", "displayName": "Root"}])
        del xr["spec"]["parentManagementGroupId"]
        xr["spec"]["environmentConfigRef"] = {"name": "azure-platform"}
        parent = "/providers/Microsoft.Management/managementGroups/configured-parent"
        required = {"environmentConfig": {"items": [{"resource": {
            "apiVersion": "apiextensions.crossplane.io/v1beta1",
            "kind": "EnvironmentConfig", "metadata": {"name": "azure-platform"},
            "data": {"parentManagementGroupId": parent},
        }}]}}
        state = self.render(xr, required=required)
        root = state["resources"]["rootGroups-example-root"]["resource"]
        self.assertEqual(root["spec"]["forProvider"]["parentManagementGroupId"], parent)
        self.assertFalse(any(
            item["resource"]["kind"] == "EnvironmentConfig"
            for item in state["resources"].values()
        ))
        observed = observe(state)
        changed = copy.deepcopy(required)
        changed["environmentConfig"]["items"][0]["resource"]["data"][
            "parentManagementGroupId"
        ] = "/providers/Microsoft.Management/managementGroups/changed"
        with self.assertRaisesRegex(AssertionError, "Changing the EnvironmentConfig parent"):
            self.render(xr, observed=observed, required=changed)
        for invalid in ("", "not-an-arm-id"):
            required["environmentConfig"]["items"][0]["resource"]["data"][
                "parentManagementGroupId"
            ] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                AssertionError, "must be a management-group resource ID",
            ):
                self.render(xr, required=required)
        required["environmentConfig"]["items"] = []
        with self.assertRaisesRegex(AssertionError, "EnvironmentConfig is missing"):
            self.render(xr, required=required)

    def settled(self, xr, required=None):
        observed = {}
        for _ in range(6):
            state = self.render(xr, observed, required)
            next_observed = observe(state)
            for item in next_observed.values():
                resource = item["resource"]
                if resource["kind"] == "XSubscription":
                    resource["status"]["managedResourceName"] = (
                        "association-" + resource["spec"]["name"]
                    )
            observed.update(next_observed)
        return state, observed

    def test_default_hierarchy_initially_publishes_only_root(self):
        rendered = self.render(self.xr())
        roots = [
            item for item in rendered["resources"].values()
            if item["resource"]["kind"] == "ManagementGroup"
        ]
        self.assertEqual(len(roots), 1)
        self.assertEqual(
            rendered["composite"]["resource"]["status"]["managementGroups"], {},
        )
        self.assertNotEqual(rendered["composite"].get("ready"), "READY_TRUE")

    def test_custom_reordered_hierarchy_ids_and_usage_edges(self):
        groups = [
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "empty", "displayName": "Empty", "archetypes": [],
             "parentRef": {"name": "root"}},
        ]
        xr = self.xr(groups)
        state = self.render(xr)
        observed = observe(state)
        state = self.render(xr, observed)
        observed.update(observe(state))
        state = self.render(xr, observed)
        status = state["composite"]["resource"]["status"]
        self.assertEqual(set(status["managementGroups"]), {"root", "empty"})
        usages = [r["resource"] for r in state["resources"].values() if r["resource"]["kind"] == "Usage"]
        self.assertTrue(any(
            usage["spec"]["of"]["resourceRef"]["name"] == "example-root"
            and usage["spec"]["by"]["resourceRef"]["name"] == "example-empty"
            for usage in usages
        ))
        reordered = copy.deepcopy(xr)
        reordered["spec"]["managementGroups"].reverse()
        self.assertEqual(
            set(state["resources"]), set(self.render(reordered, observed)["resources"]),
        )

    def test_empty_and_omitted_root_archetypes_are_policy_free_and_ready(self):
        for selection in (None, []):
            with self.subTest(selection=selection):
                root = {"name": "root", "displayName": "Root"}
                if selection is not None:
                    root["archetypes"] = selection
                xr = self.xr([
                    root, {"name": "child", "displayName": "Child",
                           "parentRef": {"name": "root"}},
                ])
                pending = self.render(xr)
                self.assertNotEqual(pending["composite"].get("ready"), "READY_TRUE")
                state, observed = self.settled(xr)
                self.assertEqual(
                    {item["resource"]["kind"] for item in state["resources"].values()},
                    {"ManagementGroup", "Usage"},
                )
                self.assertEqual(
                    set(state["composite"]["resource"]["status"]["managementGroups"]),
                    {"root", "child"},
                )
                self.assertEqual(
                    state["composite"]["resource"]["status"]["id"],
                    observed["rootGroups-example-root"]["resource"]["status"]["atProvider"]["id"],
                )
                self.assertNotEqual(state["composite"].get("ready"), "READY_FALSE")
                self.assertTrue(all(
                    item.get("ready") == "READY_TRUE" for item in state["resources"].values()
                ))
                usage_name = "usage-rootGroups-example-root-managementGroups-example-child"
                usage = state["resources"][usage_name]["resource"]
                self.assertEqual(usage["spec"]["of"]["resourceRef"]["name"], "example-root")
                self.assertEqual(usage["spec"]["by"]["resourceRef"]["name"], "example-child")
                observed[usage_name]["resource"]["status"]["conditions"][0]["status"] = "False"
                self.assertNotEqual(
                    self.render(xr, observed)["composite"].get("ready"), "READY_TRUE",
                )

    def test_root_can_select_an_alternate_archetype(self):
        xr = self.xr([{"name": "root", "displayName": "Root", "archetypes": ["sandbox"]}])
        state, _ = self.settled(xr)
        expected = {
            "assignment" + generator.token(name)
            for name, assignment in generator.compile_profile(
                json.loads((API / "library.json").read_text())
            )[2].items() if "sandbox" in assignment["archetypes"]
        }
        actual = {
            name.split("-", 1)[0] for name, item in state["resources"].items()
            if item["resource"]["kind"] == "ManagementGroupPolicyAssignment"
        }
        self.assertTrue(expected)
        self.assertEqual(actual, expected)
        self.assertNotEqual(state["composite"].get("ready"), "READY_FALSE")
        self.assertTrue(all(
            item.get("ready") == "READY_TRUE" for item in state["resources"].values()
        ))

    def test_clearing_archetypes_removes_policies_but_preserves_hierarchy(self):
        xr = self.xr([
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "child", "displayName": "Child", "parentRef": {"name": "root"}},
        ])
        fields = json.loads((API / "profile.json").read_text())["requiredInputs"]
        xr["spec"]["policyDefaults"] = {name: "/synthetic/input" for name in fields}
        before, observed = self.settled(xr)
        self.assertTrue(any(
            item["resource"]["kind"] == "ManagementGroupPolicyAssignment"
            for item in before["resources"].values()
        ))
        xr["spec"]["managementGroups"][0]["archetypes"] = []
        del xr["spec"]["policyDefaults"]
        after = self.render(xr, observed)
        def hierarchy(state):
            return {
                name: item["resource"]["spec"] for name, item in state["resources"].items()
                if item["resource"]["kind"] == "ManagementGroup"
                or (item["resource"]["kind"] == "Usage"
                    and item["resource"]["spec"]["by"]["kind"] == "ManagementGroup")
            }

        self.assertEqual(
            {item["resource"]["kind"] for item in after["resources"].values()},
            {"ManagementGroup", "Usage"},
        )
        self.assertEqual(hierarchy(after), hierarchy(before))
        self.assertEqual(
            before["composite"]["resource"]["status"]["id"],
            after["composite"]["resource"]["status"]["id"],
        )
        self.assertNotEqual(after["composite"].get("ready"), "READY_FALSE")
        # Sequencer keeps protection until the previously observed policies disappear.
        completed = self.render(xr, observe(after))
        self.assertEqual(
            {name: item["resource"]["spec"] for name, item in completed["resources"].items()},
            hierarchy(before),
        )

    def test_policy_free_team_example_settles_without_policy_inputs(self):
        example = yaml.safe_load(
            (PROJECT.parent / "teams/alz-architecture/landingzone.yaml").read_text()
        )
        xr = self.xr(example["spec"]["managementGroups"])
        del xr["spec"]["parentManagementGroupId"]
        xr["spec"]["environmentConfigRef"] = example["spec"]["environmentConfigRef"]
        required = {"environmentConfig": {"items": [{"resource": {
            "apiVersion": "apiextensions.crossplane.io/v1beta1",
            "kind": "EnvironmentConfig", "metadata": {"name": "azure-platform"},
            "data": {"parentManagementGroupId":
                     "/providers/Microsoft.Management/managementGroups/external"},
        }}]}}
        state, _ = self.settled(xr, required)
        resources = [item["resource"] for item in state["resources"].values()]
        self.assertEqual(sum(r["kind"] == "ManagementGroup" for r in resources), 12)
        self.assertEqual({r["kind"] for r in resources}, {"ManagementGroup", "Usage"})
        expected_edges = {
            ("example-" + group["parentRef"]["name"], "example-" + group["name"])
            for group in xr["spec"]["managementGroups"] if group.get("parentRef")
        }
        self.assertEqual(len(expected_edges), 11)
        root = next(g["name"] for g in xr["spec"]["managementGroups"] if not g.get("parentRef"))
        # Sequencer also protects every descendant against deletion of the ALZ root.
        expected_edges |= {
            ("example-" + root, "example-" + group["name"])
            for group in xr["spec"]["managementGroups"] if group.get("parentRef")
        }
        self.assertEqual({
            (r["spec"]["of"]["resourceRef"]["name"], r["spec"]["by"]["resourceRef"]["name"])
            for r in resources if r["kind"] == "Usage"
        }, expected_edges)
        self.assertEqual(len(state["composite"]["resource"]["status"]["managementGroups"]), 12)
        self.assertNotEqual(state["composite"].get("ready"), "READY_FALSE")
        self.assertTrue(all(
            item.get("ready") == "READY_TRUE" for item in state["resources"].values()
        ))

    def test_cycles_and_disconnected_graphs_are_fatal(self):
        groups = [
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "aa", "displayName": "A", "archetypes": [],
             "parentRef": {"name": "bb"}},
            {"name": "bb", "displayName": "B", "archetypes": [],
             "parentRef": {"name": "aa"}},
        ]
        with self.assertRaisesRegex(AssertionError, "Cycle"):
            self.render(self.xr(groups))

    def test_custom_hierarchy_only_creates_selected_policy_assets(self):
        xr = self.xr([
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
        ])
        fields = json.loads((API / "profile.json").read_text())["requiredInputs"]
        xr["spec"]["policyDefaults"] = {name: "/synthetic/input" for name in fields}
        observed = {}
        for _ in range(5):
            state = self.render(xr, observed)
            observed.update(observe(state))
        source = json.loads((API / "library.json").read_text())
        _, initiatives, assignments, _ = generator.compile_profile(source)
        selected = {
            assignment["initiative"] for assignment in assignments.values()
            if "root" in assignment["archetypes"]
        }
        selected_definitions = {
            member["policyDefinitionId"].split(":", 1)[1]
            for name in selected for member in initiatives[name]["members"]
            if member["policyDefinitionId"].startswith("@definition:")
        }
        for kind, expected in (
            ("PolicySetDefinition", selected), ("PolicyDefinition", selected_definitions),
        ):
            actual = {
                resource["resource"]["metadata"]["annotations"]["crossplane.io/external-name"]
                for resource in state["resources"].values() if resource["resource"]["kind"] == kind
            }
            self.assertEqual(actual, expected)

    def test_unknown_parents_and_excessive_depth_are_fatal(self):
        root = {"name": "root", "displayName": "Root", "archetypes": ["root"]}
        with self.assertRaisesRegex(AssertionError, "Unknown parent"):
            self.render(self.xr([
                root, {"name": "child", "displayName": "Child", "archetypes": [],
                       "parentRef": {"name": "missing"}},
            ]))
        groups = [root]
        for index in range(1, 7):
            groups.append({
                "name": f"level{index}", "displayName": f"Level {index}", "archetypes": [],
                "parentRef": {"name": groups[-1]["name"]},
            })
        with self.assertRaisesRegex(AssertionError, "six management-group levels"):
            self.render(self.xr(groups))

    def test_subscriptions_use_observed_target_ids(self):
        xr = self.xr([
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "empty", "displayName": "Empty", "archetypes": [],
             "parentRef": {"name": "root"}},
        ])
        xr["spec"]["subscriptions"] = [{
            "name": name, "subscriptionId": f"{number:08d}-1111-4111-8111-111111111111",
            "managementGroupRef": {"name": name},
        } for number, name in enumerate(("root", "empty"), start=1)]
        observed = {}
        for _ in range(4):
            state = self.render(xr, observed)
            next_observed = observe(state)
            for item in next_observed.values():
                resource = item["resource"]
                if resource["kind"] == "XSubscription":
                    resource["status"]["managedResourceName"] = (
                        "association-" + resource["spec"]["name"]
                    )
            observed.update(next_observed)
        groups = state["composite"]["resource"]["status"]["managementGroups"]
        attachments = [
            item["resource"] for item in state["resources"].values()
            if item["resource"]["kind"] == "XSubscription"
        ]
        self.assertEqual(len(attachments), 2)
        for attachment in attachments:
            target = attachment["metadata"]["name"].removeprefix("example-")
            self.assertEqual(attachment["spec"]["name"], target)
            self.assertEqual(attachment["spec"]["managementGroupId"], groups[target]["id"])
        usages = [
            item["resource"] for item in state["resources"].values()
            if item["resource"]["kind"] == "Usage"
            and item["resource"]["spec"]["by"]["kind"]
            == "ManagementGroupSubscriptionAssociation"
        ]
        self.assertEqual(len(usages), 2)
        for usage in usages:
            self.assertEqual(
                usage["spec"]["by"]["apiVersion"], "management.azure.m.upbound.io/v1beta1",
            )
            self.assertEqual(
                usage["spec"]["by"]["kind"], "ManagementGroupSubscriptionAssociation",
            )
            self.assertFalse(
                usage["metadata"]["annotations"].get("gotemplating.fn.crossplane.io/ready")
            )
        self.assertEqual(
            {
                usage["spec"]["by"]["resourceRef"]["name"] for usage in usages
            },
            {
                "association-" + attachment["spec"]["name"] for attachment in attachments
            },
        )

    def test_full_default_profile_and_missing_input_readiness(self):
        xr = self.xr()
        observed = {}
        partial = None
        for _ in range(5):
            partial = self.render(xr, observed)
            observed.update(observe(partial))
        self.assertEqual(len(partial["composite"]["resource"]["status"]["managementGroups"]), 12)
        self.assertNotEqual(partial["composite"].get("ready"), "READY_TRUE")
        fields = json.loads((API / "profile.json").read_text())["requiredInputs"]
        xr["spec"]["policyDefaults"] = {name: "/synthetic/input" for name in fields}
        xr["spec"]["policyDefaults"]["resourceGroupLocation"] = "swedencentral"
        complete = None
        for _ in range(5):
            complete = self.render(xr, observed)
            observed.update(observe(complete))
        self.assertNotEqual(complete["composite"].get("ready"), "READY_FALSE")
        self.assertTrue(all(
            resource.get("ready") == "READY_TRUE"
            for resource in complete["resources"].values()
        ))
        self.assertLess(len(json.dumps(complete).encode()), 4 * 1024 * 1024)
        self.assertTrue(any(
            r["resource"]["kind"] == "ManagementGroupPolicyAssignment"
            for r in complete["resources"].values()
        ))

    def test_association_usage_survives_disappearing_subscription_xr(self):
        xr = self.xr([{"name": "root", "displayName": "Root", "archetypes": ["root"]}])
        xr["spec"]["subscriptions"] = [{
            "name": "attachment",
            "subscriptionId": "11111111-1111-4111-8111-111111111111",
            "managementGroupRef": {"name": "root"},
        }]
        _, observed = self.settled(xr)
        usage_name = next(
            name for name, item in observed.items()
            if item["resource"]["kind"] == "Usage"
            and item["resource"]["spec"]["by"]["kind"]
            == "ManagementGroupSubscriptionAssociation"
        )
        usage = observed[usage_name]["resource"]
        for name in list(observed):
            if observed[name]["resource"]["kind"] == "XSubscription":
                del observed[name]
        xr["spec"]["subscriptions"] = []
        state = self.render(xr, observed)
        preserved = state["resources"][usage_name]["resource"]
        self.assertEqual(preserved["spec"], usage["spec"])
        self.assertEqual(preserved["metadata"]["name"], usage["metadata"]["name"])
        del observed[usage_name]
        self.assertNotIn(usage_name, self.render(xr, observed)["resources"])

    def test_hierarchy_usage_readiness_is_observed_not_forced(self):
        xr = self.xr([
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "child", "displayName": "Child", "archetypes": [],
             "parentRef": {"name": "root"}},
        ])
        inputs = json.loads((API / "profile.json").read_text())["requiredInputs"]
        xr["spec"]["policyDefaults"] = {name: "/synthetic/input" for name in inputs}
        _, observed = self.settled(xr)
        usage_name = "usage-rootGroups-example-root-managementGroups-example-child"
        observed[usage_name]["resource"]["status"]["conditions"] = [
            {"type": "Ready", "status": "False"},
        ]
        pending = self.render(xr, observed)
        self.assertNotEqual(pending["composite"].get("ready"), "READY_TRUE")
        self.assertNotEqual(pending["resources"][usage_name].get("ready"), "READY_TRUE")
        observed[usage_name]["resource"]["status"]["conditions"][0]["status"] = "True"
        ready = self.render(xr, observed)
        self.assertNotEqual(ready["composite"].get("ready"), "READY_FALSE")
        self.assertTrue(all(
            item.get("ready") == "READY_TRUE" for item in ready["resources"].values()
        ))

    def test_maximum_prefix_and_attachment_names_keep_child_spec_within_limit(self):
        xr = self.xr([{"name": "root", "displayName": "Root", "archetypes": ["root"]}])
        xr["spec"]["name"] = "a" * 20
        xr["spec"]["subscriptions"] = [{
            "name": "b" * 32,
            "subscriptionId": "11111111-1111-4111-8111-111111111111",
            "managementGroupRef": {"name": "root"},
        }]
        state = self.render(xr)
        state = self.render(xr, observe(state))
        child = next(
            item["resource"] for item in state["resources"].values()
            if item["resource"]["kind"] == "XSubscription"
        )
        self.assertEqual(child["spec"]["name"], "b" * 32)
        self.assertEqual(child["metadata"]["name"], "a" * 20 + "-" + "b" * 32)
        self.assertLessEqual(len(child["spec"]["name"]), 40)

    def test_removal_of_observed_groups_fails_without_pruning(self):
        xr = self.xr([
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "empty", "displayName": "Empty", "archetypes": [],
             "parentRef": {"name": "root"}},
        ])
        observed = observe(self.render(xr))
        observed.update(observe(self.render(xr, observed)))
        xr["spec"]["managementGroups"].pop()
        with self.assertRaisesRegex(AssertionError, "Removing existing"):
            self.render(xr, observed)

    def test_reparenting_observed_group_fails_without_pruning(self):
        xr = self.xr([
            {"name": "root", "displayName": "Root", "archetypes": ["root"]},
            {"name": "first", "displayName": "First", "archetypes": [],
             "parentRef": {"name": "root"}},
            {"name": "second", "displayName": "Second", "archetypes": [],
             "parentRef": {"name": "root"}},
        ])
        observed = observe(self.render(xr))
        observed.update(observe(self.render(xr, observed)))
        xr["spec"]["managementGroups"][2]["parentRef"]["name"] = "first"
        with self.assertRaisesRegex(AssertionError, "Reparenting"):
            self.render(xr, observed)
