"""Opt-in direct gRPC rendering against pinned local function runtimes."""

import copy
import gzip
import json
from pathlib import Path

import yaml


def defaults(value, schema):
    if isinstance(value, dict):
        for name, child in schema.get("properties", {}).items():
            if name not in value and "default" in child:
                value[name] = copy.deepcopy(child["default"])
            if name in value:
                defaults(value[name], child)
    elif isinstance(value, list):
        for child in value:
            defaults(child, schema.get("items", {}))
    return value


def schemas(paths):
    result = {}
    for path in paths:
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as stream:
            for document in yaml.safe_load_all(stream):
                if not document:
                    continue
                if document["kind"] not in (
                    "CompositeResourceDefinition", "CustomResourceDefinition",
                ):
                    continue
                spec = document["spec"]
                for version in spec["versions"]:
                    schema = copy.deepcopy(version["schema"]["openAPIV3Schema"])
                    schema.setdefault("properties", {}).update({
                        "apiVersion": {"type": "string"}, "kind": {"type": "string"},
                        "metadata": {
                            "type": "object",
                            "properties": {
                                **{name: {"type": "string"} for name in (
                                    "name", "namespace", "uid",
                                )},
                                "annotations": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "labels": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                        },
                    })
                    key = f"{spec['group']}/{version['name']}, Kind={spec['names']['kind']}"
                    result[key] = {"openapiV3": schema}
    return result


def run_pipeline(composition, xr, schema_map, endpoints, observed=None, required=None):
    import grpc
    from crossplane.function.proto.v1 import run_function_pb2 as protocol
    from crossplane.function.proto.v1 import run_function_pb2_grpc as service
    from google.protobuf.json_format import MessageToDict, ParseDict

    desired = {"composite": {"resource": copy.deepcopy(xr)}, "resources": {}}
    context = {}
    for step in composition["spec"]["pipeline"]:
        name = step["functionRef"]["name"]
        if name not in endpoints:
            raise ValueError(f"No test endpoint for {name}")
        request = {
            "meta": {"capabilities": ["CAPABILITY_REQUIRED_SCHEMAS"]},
            "observed": {
                "composite": {"resource": xr}, "resources": observed or {},
            },
            "desired": desired, "context": context,
            "requiredSchemas": schema_map,
            "requiredResources": required or {},
        }
        if "input" in step:
            request["input"] = step["input"]
        options = [("grpc.max_receive_message_length", 16 * 1024 * 1024)]
        with grpc.insecure_channel(endpoints[name], options=options) as channel:
            response = service.FunctionRunnerServiceStub(channel).RunFunction(
                ParseDict(request, protocol.RunFunctionRequest()), timeout=90,
            )
        response = MessageToDict(response)
        for result in response.get("results", []):
            if result.get("severity") == "SEVERITY_FATAL":
                raise AssertionError(f"{step['step']}: {result.get('message')}")
        desired = response.get("desired", desired)
        context = response.get("context", context)
    return desired


def observe(desired):
    """Provide deterministic synthetic Azure IDs and Ready=True, never live state."""
    resources = copy.deepcopy(desired.get("resources", {}))
    for key, resource in resources.items():
        value = resource["resource"]
        value.setdefault("metadata", {})["name"] = value.get("metadata", {}).get(
            "name", "test-" + str(len(key)) + "-" + key[-40:].lower(),
        )
        value["metadata"]["namespace"] = "alz-test"
        value["status"] = {
            "atProvider": {"id": "/synthetic/" + key},
            "conditions": [{"type": "Ready", "status": "True"}],
        }
    return resources
