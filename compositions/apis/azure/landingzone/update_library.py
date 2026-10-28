"""Refresh the reviewed ALZ input snapshot; ordinary generation is offline."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ALZ_COMMIT = "4eae565287844360a61545f9799400431adb6d0a"
POLICY_COMMIT = "99313872e9f5095d5da76d22377f01b016da35a6"


def archive(repo, commit):
    data = subprocess.check_output(["gh", "api", f"repos/{repo}/tarball/{commit}"])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
        for member in source.getmembers():
            if member.isfile():
                files[member.name.split("/", 1)[1]] = source.extractfile(member).read()
    return files


def refresh():
    alz = archive("Azure/azure-landing-zones-library", ALZ_COMMIT)
    builtin = archive("Azure/azure-policy", POLICY_COMMIT)
    assets = {}
    for path, data in alz.items():
        if path.startswith("platform/alz/") and path.endswith(".json"):
            asset = json.loads(data)
            if "/policy_definitions/" in path:
                assets["policyDefinitions/" + asset["name"].lower()] = asset
            elif "/policy_set_definitions/" in path:
                assets["policySetDefinitions/" + asset["name"].lower()] = asset
    available = {}
    for path, data in builtin.items():
        if not path.startswith("built-in-policies/") or not path.endswith(".json"):
            continue
        if "/Azure Government/" in path or "/Azure China/" in path:
            continue
        asset = json.loads(data)
        if not isinstance(asset, dict) or "name" not in asset:
            continue
        if "/policyDefinitions/" in path:
            available["policyDefinitions/" + asset["name"].lower()] = asset
        elif "/policySetDefinitions/" in path:
            available["policySetDefinitions/" + asset["name"].lower()] = asset

    archetypes = [
        json.loads(data) for path, data in sorted(alz.items())
        if path.startswith("platform/alz/archetype_definitions/")
        and path.endswith(".json")
    ]
    assignments = {
        asset["name"]: asset
        for path, data in sorted(alz.items())
        if path.startswith("platform/alz/policy_assignments/")
        and path.endswith(".json")
        for asset in [json.loads(data)]
    }
    used = {}

    def include(resource_id):
        key = "/".join(resource_id.split("/")[-2:])
        key = key.split("/")[0] + "/" + key.split("/")[1].lower()
        if key in used:
            return
        asset = assets.get(key) or available.get(key)
        if asset is None:
            raise ValueError(f"Unresolved policy reference: {resource_id}")
        used[key] = asset
        for member in asset["properties"].get("policyDefinitions", []):
            include(member["policyDefinitionId"])

    names = {name for a in archetypes for name in a["policy_assignments"]}
    for name in sorted(names):
        include(assignments[name]["properties"]["policyDefinitionId"])
    snapshot = {
        "sources": {
            "alz": {
                "repository": "Azure/azure-landing-zones-library",
                "release": "platform/alz/2026.10.0",
                "commit": ALZ_COMMIT,
            },
            "builtinPolicies": {
                "repository": "Azure/azure-policy",
                "commit": POLICY_COMMIT,
            },
        },
        "architecture": json.loads(
            alz["platform/alz/architecture_definitions/alz.alz_architecture_definition.json"]
        ),
        "archetypes": archetypes,
        "assignments": {name: assignments[name] for name in sorted(names)},
        "policies": dict(sorted(used.items())),
        "defaults": json.loads(alz["platform/alz/alz_policy_default_values.json"]),
    }
    encoded = (json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n").encode()
    (ROOT / "library.json").write_bytes(encoded)
    lock = {
        "sources": snapshot["sources"],
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    (ROOT / "library-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    for name, files in (("ALZ", alz), ("AZURE-POLICY", builtin)):
        (ROOT / f"LICENSE-{name}").write_bytes(files["LICENSE"])
    print(f"Locked {len(used)} policy assets and {len(names)} assignments.")


if __name__ == "__main__":
    refresh()
