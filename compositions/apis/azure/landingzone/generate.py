"""Compile a pinned ALZ snapshot to an offline, audit/deny-only composition."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
SAFE = {"audit": "Audit", "auditifnotexists": "AuditIfNotExists", "deny": "Deny"}
UNSAFE = {
    "deployifnotexists", "modify", "append", "denyaction", "disabled", "manual",
    "enforcesetting", "addtonetworkgroup", "mutate",
}
PARAMETER = re.compile(r"^\[parameters\('([^']+)'\)\]$", re.IGNORECASE)
CONDITIONAL_EFFECT = re.compile(
    r"^\[if\(contains\(parameters\('([^']+)'\),'([^']+)'\),"
    r"parameters\('([^']+)'\),'Disabled'\)\]$", re.IGNORECASE,
)
GROUP = "azure.platform.example.org/v1alpha1"
AUTH = "authorization.azure.m.upbound.io/v1beta1"
MG = "management.azure.m.upbound.io/v1beta1"


class ManifestDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def represent_string(dumper, value):
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None,
    )


ManifestDumper.add_representer(str, represent_string)


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def token(value):
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def projected_display_name(value):
    suffix = " (audit/deny only)"
    return value[:128 - len(suffix)] + suffix


def public_name(value):
    return value.replace("_", "-")


def parameter_values(definition, overrides):
    values = {
        key: copy.deepcopy(spec["defaultValue"])
        for key, spec in definition.get("parameters", {}).items()
        if "defaultValue" in spec
    }
    for key, value in overrides.items():
        canonical = next(
            (name for name in definition.get("parameters", {}) if name.lower() == key.lower()), key,
        )
        values[canonical] = copy.deepcopy(value["value"])
    return values


def lookup(parameters, name):
    matches = [value for key, value in parameters.items() if key.lower() == name.lower()]
    if len(matches) != 1:
        raise ValueError(f"Unresolved or ambiguous parameter: {name}")
    return matches[0]


def resolve(value, parameters):
    if isinstance(value, dict):
        return {key: resolve(child, parameters) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve(child, parameters) for child in value]
    if isinstance(value, str):
        conditional = CONDITIONAL_EFFECT.fullmatch(value)
        if conditional:
            choices = lookup(parameters, conditional[1])
            if not isinstance(choices, (list, dict, str)):
                raise ValueError(f"Invalid contains input: {value}")
            return lookup(parameters, conditional[3]) if conditional[2] in choices else "Disabled"
        match = PARAMETER.fullmatch(value)
        if match:
            return copy.deepcopy(lookup(parameters, match[1]))
        if value.startswith("[") and "parameters(" in value.lower():
            raise ValueError(f"Unsupported parameter expression: {value}")
    return value


def definition_version(version):
    """Map a metadata version to Azure's '{major}.{minor}.*[-preview]' reference form."""
    match = re.fullmatch(r"([1-9]\d*)\.(\d+)\.\d+(?:-(preview|deprecated))?", version)
    if not match:
        raise ValueError(f"Unsupported policy definition version: {version}")
    # '-deprecated' is a metadata label; Azure publishes the version without it.
    return f"{match[1]}.{match[2]}.*" + ("-preview" if match[3] == "preview" else "")


def policy_key(resource_id):
    parts = resource_id.split("/")
    return parts[-2] + "/" + parts[-1].lower()


def selected_effect(properties, overrides):
    expression = properties["policyRule"]["then"]["effect"]
    values = parameter_values(properties, overrides)
    effect = resolve(expression, values)
    if not isinstance(effect, str):
        raise ValueError(f"Non-string policy effect: {effect}")
    match = PARAMETER.fullmatch(expression)
    allowed = properties["parameters"][match[1]].get("allowedValues", []) if match else []
    if effect.lower() in SAFE and (
        not allowed or any(v.lower() == effect.lower() for v in allowed)
    ):
        return SAFE[effect.lower()], values, False
    if effect.lower() in SAFE:
        effect = "disabled" if not match else "modify"
    if effect.lower() not in UNSAFE:
        raise ValueError(f"Unknown policy effect: {effect}")
    if effect.lower() == "disabled":
        return None, values, False
    if match:
        alternatives = {v.lower(): v for v in allowed if isinstance(v, str)}
        for audit in ("auditifnotexists", "audit"):
            if audit in alternatives:
                values[match[1]] = alternatives[audit]
                return SAFE[audit], values, True
    return None, values, False


def compile_profile(source):
    policies = source["policies"]
    definitions, initiatives, assignments, report = {}, {}, {}, []
    default_mapping = {}
    for default in source["defaults"]["defaults"]:
        field = re.sub(r"_([a-z])", lambda m: m[1].upper(), default["default_name"])
        for assignment in default["policy_assignments"]:
            for name in assignment["parameter_names"]:
                default_mapping[(assignment["policy_assignment_name"], name)] = field
    # This guardrail uses the same plan input but is absent from ALZ's defaults map.
    default_mapping[("Enforce-GR-Network0", "ddosPlanResourceId")] = "ddosProtectionPlanId"

    def leaf(resource_id, overrides, assignment_name):
        key = policy_key(resource_id)
        definition = policies[key]
        properties = definition["properties"]
        overrides = copy.deepcopy(overrides)
        if key == "policyDefinitions/82985f06-dc18-4a48-bc1c-b9f4f0098cfe":
            # The pinned built-in v8 replaces v6's port bounds with range strings.
            if "minPort" in overrides and "maxPort" in overrides:
                minimum = overrides.pop("minPort")["value"]
                maximum = overrides.pop("maxPort")["value"]
                overrides["allowedHostPorts"] = {"value": [f"{minimum}-{maximum}"]}
                report.append({
                    "assignment": assignment_name, "policy": key,
                    "action": "host-port-v8-parameter-migration",
                })
        unknown = set(overrides) - set(properties.get("parameters", {}))
        if unknown:
            raise ValueError(f"{key}: unsupported policy parameters {sorted(unknown)}")
        effect, values, converted = selected_effect(properties, overrides)
        if effect is None:
            report.append({"assignment": assignment_name, "policy": key, "action": "omitted"})
            return None
        values = {name: resolve(value, {}) for name, value in values.items()}
        missing = set(properties.get("parameters", {})) - set(values)
        if missing:
            raise ValueError(f"{key}: missing required policy parameters {sorted(missing)}")
        match = PARAMETER.fullmatch(properties["policyRule"]["then"]["effect"])
        if match:
            values[match[1]] = effect
        if not definition.get("id"):
            name = "safe-" + token(key + effect)
            desired = copy.deepcopy(properties)
            desired["policyRule"]["then"]["effect"] = effect
            # Azure rejects definitions declaring parameters the rule no longer uses.
            rule = json.dumps(desired["policyRule"]).lower()
            for parameter in list(desired.get("parameters", {})):
                if f"parameters('{parameter.lower()}')" not in rule:
                    del desired["parameters"][parameter]
                    values.pop(parameter, None)
            definitions[name] = desired
            resource_id = "@definition:" + name
        report.append({
            "assignment": assignment_name, "policy": key, "effect": effect,
            "action": "audit-alternative" if converted else "retained",
        })
        return {
            "policyDefinitionId": resource_id,
            "parameters": {name: {"value": value} for name, value in values.items()},
            "version": properties.get("metadata", {}).get("version"),
        }

    for name, assignment in sorted(source["assignments"].items()):
        properties = assignment["properties"]
        definition = policies[policy_key(properties["policyDefinitionId"])]
        defaults = parameter_values(definition["properties"], properties.get("parameters", {}))
        assignment_overrides = []
        for override in properties.get("overrides", []):
            if set(override) - {"kind", "value"} or override.get("kind") != "policyEffect":
                raise ValueError(f"{name}: unsupported assignment override {override}")
            effect = override.get("value")
            if not isinstance(effect, str) or effect.lower() not in SAFE:
                raise ValueError(f"{name}: unsupported policyEffect override {effect}")
            assignment_overrides.append({"value": SAFE[effect.lower()]})
            report.append({
                "assignment": name, "effect": SAFE[effect.lower()],
                "action": "assignment-policy-effect-override-retained",
            })
        for parameter, value in list(defaults.items()):
            field = default_mapping.get((name, parameter))
            if field:
                defaults[parameter] = {"$input": field}
        members = []
        if "policyDefinitions" in definition["properties"]:
            for member in definition["properties"]["policyDefinitions"]:
                leaf_def = policies[policy_key(member["policyDefinitionId"])]["properties"]
                # Resolve the effect before evaluating unrelated DINE deployment inputs.
                expression = leaf_def["policyRule"]["then"]["effect"]
                effect_parameter = PARAMETER.fullmatch(expression)
                effect_overrides = {}
                if effect_parameter and effect_parameter[1] in member.get("parameters", {}):
                    effect_overrides[effect_parameter[1]] = {
                        "value": resolve(member["parameters"][effect_parameter[1]]["value"], defaults)
                    }
                safe, _, _ = selected_effect(leaf_def, effect_overrides)
                if safe is None:
                    report.append({
                        "assignment": name,
                        "policy": policy_key(member["policyDefinitionId"]),
                        "action": "omitted",
                    })
                    continue
                overrides = resolve(member.get("parameters", {}), defaults)
                result = leaf(member["policyDefinitionId"], overrides, name)
                if result:
                    result["referenceId"] = member["policyDefinitionReferenceId"]
                    members.append(result)
        else:
            result = leaf(
                properties["policyDefinitionId"],
                {key: {"value": value} for key, value in defaults.items()}, name,
            )
            if result:
                result["referenceId"] = "policy"
                members.append(result)
        if members:
            enforcement = properties.get("enforcementMode", "Default")
            if enforcement not in ("Default", "DoNotEnforce"):
                raise ValueError(f"{name}: unsupported enforcement mode {enforcement}")
            references = {member["referenceId"] for member in members}
            messages = [
                {
                    "content": message["message"].replace(
                        "{enforcementMode}", "must" if enforcement == "Default" else "should",
                    ),
                    **({"policyDefinitionReferenceId": message["policyDefinitionReferenceId"]}
                       if "policyDefinitionReferenceId" in message else {}),
                }
                for message in properties.get("nonComplianceMessages", [])
                if "policyDefinitionReferenceId" not in message
                or message["policyDefinitionReferenceId"] in references
            ]
            initiative_name = "safe-" + token(name)
            initiatives[initiative_name] = {
                "displayName": projected_display_name(properties["displayName"]),
                "members": members,
            }
            assignments[name] = {
                "initiative": initiative_name,
                "displayName": properties["displayName"],
                "enforce": enforcement == "Default",
                "nonComplianceMessage": messages,
                "archetypes": sorted(
                    public_name(archetype["name"])
                    for archetype in source["archetypes"]
                    if name in archetype["policy_assignments"]
                ),
            }
            if assignment_overrides:
                assignments[name]["overrides"] = assignment_overrides
        else:
            report.append({"assignment": name, "action": "empty-initiative-omitted"})
    return definitions, initiatives, assignments, report


def cel(value, inputs):
    if isinstance(value, dict):
        if set(value) == {"$input"}:
            inputs.add(value["$input"])
            return "schema.spec.?policyDefaults.?%s.orValue(\"\")" % value["$input"]
        return "{" + ",".join(compact(k) + ":" + cel(v, inputs) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(cel(v, inputs) for v in value) + "]"
    if isinstance(value, str) and ("placeholder" in value.lower() or "${" in value):
        raise ValueError(f"Unmapped deployment placeholder: {value}")
    return compact(value)


def expr(value):
    return "${" + value + "}"


def resource(node_id, kind, api_version, provider, external_name=None):
    template = {
        "apiVersion": api_version, "kind": kind, "metadata": {},
        "spec": {"providerConfigRef": provider, "forProvider": {}},
    }
    if external_name:
        template["metadata"]["annotations"] = {"crossplane.io/external-name": external_name}
    return {"id": node_id, "template": template}


def selected_archetypes(archetypes):
    return expr(
        "schema.spec.managementGroups.exists(g, g.archetypes.exists(a, a in "
        + compact(sorted(archetypes)) + "))"
    )


def generate():
    raw = (ROOT / "library.json").read_bytes()
    lock = json.loads((ROOT / "library-lock.json").read_text())
    if hashlib.sha256(raw).hexdigest() != lock["sha256"]:
        raise ValueError("library.json differs from its reviewed source lock")
    source = json.loads(raw)
    definitions, initiatives, assignments, report = compile_profile(source)
    initiative_archetypes = {
        assignment["initiative"]: assignment["archetypes"] for assignment in assignments.values()
    }
    definition_archetypes = {name: set() for name in definitions}
    for name, initiative in initiatives.items():
        for member in initiative["members"]:
            if member["policyDefinitionId"].startswith("@definition:"):
                definition_name = member["policyDefinitionId"].split(":", 1)[1]
                definition_archetypes[definition_name].update(initiative_archetypes[name])
    provider = {
        "kind": expr('schema.spec.?providerConfigRef.?kind.orValue("ClusterProviderConfig")'),
        "name": expr('schema.spec.?providerConfigRef.?name.orValue("default")'),
    }
    root_id = "rootGroups[0].status.atProvider.id"
    group_name = expr('schema.spec.name + "-" + group.name')
    root = resource("rootGroups", "ManagementGroup", MG, provider, expr("schema.spec.name"))
    root["forEach"] = [{"group": expr("schema.spec.managementGroups.filter(g, !has(g.parentRef))")}]
    root["template"]["metadata"]["name"] = group_name
    root["template"]["spec"]["forProvider"] = {
        "displayName": expr("group.displayName"),
        # The final template resolves EnvironmentConfig references before apply.
        "parentManagementGroupId": expr('schema.spec.?parentManagementGroupId.orValue("")'),
    }
    children = resource(
        "managementGroups", "ManagementGroup", MG, provider,
        expr('schema.spec.name + "-" + group.name'),
    )
    children["forEach"] = [{"group": expr("schema.spec.managementGroups.filter(g, has(g.parentRef))")}]
    children["template"]["metadata"]["name"] = group_name
    children["template"]["spec"]["forProvider"] = {
        "displayName": expr("group.displayName"),
        "parentManagementGroupIdRef": {
            "name": expr('schema.spec.name + "-" + group.parentRef.name'),
            "policy": {"resolve": "Always"},
        },
    }
    resources = [root, children]
    sequences = [{"sequence": ["rootGroups-.*", "managementGroups-.*"]}]
    catalog = {"assignments": [], "definitionNodes": {}, "initiativeNodes": {}}
    all_inputs = set()
    for name, properties in sorted(definitions.items()):
        node_id = "definition" + token(name)
        catalog["definitionNodes"][name] = node_id
        node = resource(node_id, "PolicyDefinition", AUTH, provider, name)
        node["includeWhen"] = [selected_archetypes(definition_archetypes[name])]
        fp = node["template"]["spec"]["forProvider"]
        fp.update({
            "managementGroupId": expr(root_id), "policyType": "Custom",
            "displayName": properties["displayName"], "mode": properties.get("mode", "All"),
            "policyRule": compact(properties["policyRule"]),
        })
        if properties.get("parameters"):
            fp["parameters"] = compact(properties["parameters"])
        resources.append(node)
        sequences.append({"sequence": ["rootGroups-.*", node_id]})

    for name, initiative in sorted(initiatives.items()):
        node_id = "initiative" + token(name)
        catalog["initiativeNodes"][name] = node_id
        node = resource(node_id, "PolicySetDefinition", AUTH, provider, name)
        node["includeWhen"] = [selected_archetypes(initiative_archetypes[name])]
        inputs = set()
        references = []
        for member in initiative["members"]:
            resource_id = member["policyDefinitionId"]
            if resource_id.startswith("@definition:"):
                definition_name = resource_id.split(":", 1)[1]
                definition_node = catalog["definitionNodes"][definition_name]
                resource_id = expr(definition_node + ".status.atProvider.id")
                sequences.append({"sequence": [definition_node, node_id]})
            reference = {
                "policyDefinitionId": resource_id,
                "referenceId": member["referenceId"],
                "parameterValues": expr("json.marshal(" + cel(member["parameters"], inputs) + ")"),
            }
            if member["version"] and not member["policyDefinitionId"].startswith("@definition:"):
                reference["version"] = definition_version(member["version"])
            references.append(reference)
        node["template"]["spec"]["forProvider"].update({
            "managementGroupId": expr(root_id), "policyType": "Custom",
            "displayName": initiative["displayName"], "policyDefinitionReference": references,
        })
        if inputs:
            node["includeWhen"].extend(
                expr('schema.spec.?policyDefaults.?%s.orValue("") != ""' % field)
                for field in sorted(inputs)
            )
        resources.append(node)
        sequences.append({"sequence": ["rootGroups-.*", node_id]})
        assignment_name = next(key for key, value in assignments.items() if value["initiative"] == name)
        assignment = assignments[assignment_name]
        assignment_node = "assignment" + token(assignment_name)
        node = resource(
            assignment_node, "ManagementGroupPolicyAssignment", AUTH, provider,
            "safe-" + token(assignment_name),
        )
        node["forEach"] = [{
            "group": expr(
                "schema.spec.managementGroups.filter(g, g.archetypes.exists(a, a in "
                + compact(assignment["archetypes"]) + "))"
            ),
        }]
        node["template"]["metadata"]["name"] = expr(
            'schema.spec.name + "-" + group.name + "-' + token(assignment_name) + '"'
        )
        node["template"]["spec"]["forProvider"].update({
            "managementGroupIdRef": {
                "name": expr('schema.spec.name + "-" + group.name'),
            },
            "policyDefinitionId": expr(node_id + ".status.atProvider.id"),
            "displayName": projected_display_name(assignment["displayName"]),
            "enforce": assignment["enforce"],
        })
        if assignment.get("overrides"):
            node["template"]["spec"]["forProvider"]["overrides"] = assignment["overrides"]
        if assignment["nonComplianceMessage"]:
            node["template"]["spec"]["forProvider"]["nonComplianceMessage"] = (
                assignment["nonComplianceMessage"]
            )
        resources.append(node)
        sequences.append({"sequence": [node_id, assignment_node + "-.*"]})
        catalog["assignments"].append({
            "name": assignment_name, "node": assignment_node, "initiativeNode": node_id,
            "archetypes": assignment["archetypes"], "inputs": sorted(inputs),
        })
        all_inputs.update(inputs)

    subscriptions = {
        "id": "subscriptions",
        "forEach": [{"subscription": expr("schema.spec.?subscriptions.orValue([])")}],
        "template": {
            "apiVersion": GROUP, "kind": "XSubscription",
            "metadata": {"name": expr('schema.spec.name + "-" + subscription.name')},
            "spec": {
                "name": expr("subscription.name"),
                "subscriptionId": expr("subscription.subscriptionId"),
                "managementGroupId": expr(
                    'schema.spec.managementGroups.filter(g, g.name == subscription.managementGroupRef.name)'
                    '[0].?parentRef.hasValue() ? managementGroups.filter(g,'
                    ' g.metadata.name == schema.spec.name + "-" + subscription.managementGroupRef.name)'
                    '[0].status.atProvider.id : ' + root_id
                ),
                "providerConfigRef": provider,
            },
        },
    }
    resources.append(subscriptions)
    sequences.append({"sequence": ["rootGroups-.*", "subscriptions-.*"]})
    graph = {"apiVersion": "kro.fn.crossplane.io/v1alpha1", "kind": "ResourceGraph", "resources": resources}
    validation = (ROOT / "validate.gotmpl").read_text()
    status = (ROOT / "status.gotmpl").read_text().replace("__CATALOG__", compact(catalog))
    composition = {
        "apiVersion": "apiextensions.crossplane.io/v1", "kind": "Composition",
        "metadata": {"name": "landingzonearchitectures.azure.platform.example.org"},
        "spec": {
            "compositeTypeRef": {
                "apiVersion": GROUP, "kind": "XLandingZoneArchitecture",
            },
            "mode": "Pipeline",
            "pipeline": [
                {"step": "validate-hierarchy", "functionRef": {"name": "function-go-templating"},
                 "input": {"apiVersion": "gotemplating.fn.crossplane.io/v1beta1", "kind": "GoTemplate",
                           "source": "Inline", "inline": {"template": validation}}},
                {"step": "kro", "functionRef": {"name": "function-kro"}, "input": graph},
                {"step": "observe-readiness", "functionRef": {"name": "function-auto-ready"}},
                {"step": "sequence", "functionRef": {"name": "function-sequencer"},
                 "input": {"apiVersion": "sequencer.fn.crossplane.io/v1beta1", "kind": "Input",
                           "enableDeletionSequencing": True, "usageVersion": "v2",
                           "replayDeletion": True, "resetCompositeReadiness": True,
                           "rules": sequences}},
                {"step": "status-and-hierarchy-usages", "functionRef": {"name": "function-go-templating"},
                 "input": {"apiVersion": "gotemplating.fn.crossplane.io/v1beta1", "kind": "GoTemplate",
                           "source": "Inline", "inline": {"template": status}}},
                {"step": "auto-ready", "functionRef": {"name": "function-auto-ready"}},
            ],
        },
    }
    architecture = []
    for group in source["architecture"]["management_groups"]:
        entry = {
            "name": public_name(group["id"]), "displayName": group["display_name"],
            "archetypes": [public_name(name) for name in group["archetypes"]],
        }
        if group["parent_id"]:
            entry["parentRef"] = {"name": public_name(group["parent_id"])}
        architecture.append(entry)
    patch = yaml.safe_load((ROOT / "validation-schema.yaml").read_text())
    patch["properties"]["spec"]["properties"]["managementGroups"]["default"] = architecture
    patch["properties"]["spec"]["properties"]["managementGroups"]["items"]["properties"]["archetypes"]["items"]["enum"] = sorted(
        public_name(a["name"]) for a in source["archetypes"]
    )
    patch["properties"]["spec"]["properties"]["policyDefaults"] = {
        "type": "object",
        "properties": {field: {"type": "string", "minLength": 1} for field in sorted(all_inputs)},
    }
    profile = {
        "source": source["sources"], "allowedEffects": list(SAFE.values()),
        "requiredInputs": sorted(all_inputs), "assignments": catalog["assignments"],
        "decisions": report,
    }
    output = {
        "composition.yaml": "---\n# Generated by generate.py. Do not edit.\n"
        "# yamllint disable rule:line-length\n"
        + yaml.dump(composition, Dumper=ManifestDumper, sort_keys=False, width=100000),
        "xrd-patch.yaml": "---\n# Generated by generate.py. Do not edit.\n"
        "# yamllint disable rule:line-length\n"
        + yaml.dump(patch, Dumper=ManifestDumper, sort_keys=False, width=76),
        "profile.json": json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
    }
    if len(output["composition.yaml"].encode()) > 900000:
        raise ValueError("Generated composition exceeds the 900KB safety budget")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, content in generate().items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"{path} is stale; run generate.py")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
