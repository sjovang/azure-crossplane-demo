"""Resolve an ALZ release or restore its reviewed, hash-locked snapshot."""

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".build"
ALZ_REPOSITORY = "Azure/azure-landing-zones-library"
POLICY_REPOSITORY = "Azure/azure-policy"


def release_config():
    config = json.loads((ROOT / "release.json").read_text())
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d+", config["alz"]):
        raise ValueError("ALZ release must use YYYY.MM.PATCH, for example 2026.10.0")
    if not re.fullmatch(r"[0-9a-f]{40}", config["builtinPoliciesCommit"]):
        raise ValueError("builtinPoliciesCommit must be a full Git commit SHA")
    return config


def resolve_release(release):
    ref = f"tags/platform/alz/{release}"
    obj = json.loads(subprocess.check_output([
        "gh", "api", f"repos/{ALZ_REPOSITORY}/git/ref/{ref}",
    ]))["object"]
    while obj["type"] == "tag":
        obj = json.loads(subprocess.check_output([
            "gh", "api", f"repos/{ALZ_REPOSITORY}/git/tags/{obj['sha']}",
        ]))["object"]
    if obj["type"] != "commit":
        raise ValueError(f"ALZ release {release} does not resolve to a commit")
    return obj["sha"]


def archive(repo, commit):
    data = subprocess.check_output(["gh", "api", f"repos/{repo}/tarball/{commit}"])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
        for member in source.getmembers():
            if member.isfile():
                files[member.name.split("/", 1)[1]] = source.extractfile(member).read()
    return files


def refresh(locked=False, if_missing=False):
    config = release_config()
    release = "platform/alz/" + config["alz"]
    lock = json.loads((ROOT / "library-lock.json").read_text()) if locked else None
    if locked:
        sources = lock["sources"]
        if (
            sources["alz"]["release"] != release
            or sources["builtinPolicies"]["commit"] != config["builtinPoliciesCommit"]
            or sources["alz"]["repository"] != ALZ_REPOSITORY
            or sources["builtinPolicies"]["repository"] != POLICY_REPOSITORY
        ):
            raise ValueError("release.json changed; run update_library.py to review a new lock")
        snapshot = BUILD / "library.json"
        if if_missing and snapshot.exists():
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != lock["sha256"]:
                raise ValueError("library.json differs from its reviewed source lock")
            return
        alz_commit = sources["alz"]["commit"]
    else:
        alz_commit = resolve_release(config["alz"])
    alz = archive(ALZ_REPOSITORY, alz_commit)
    builtin = archive(POLICY_REPOSITORY, config["builtinPoliciesCommit"])
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
                "repository": ALZ_REPOSITORY,
                "release": release,
                "commit": alz_commit,
            },
            "builtinPolicies": {
                "repository": POLICY_REPOSITORY,
                "commit": config["builtinPoliciesCommit"],
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
    if locked and hashlib.sha256(encoded).hexdigest() != lock["sha256"]:
        raise ValueError("Restored ALZ snapshot differs from its reviewed source lock")
    BUILD.mkdir(exist_ok=True)
    (BUILD / "library.json").write_bytes(encoded)
    lock = {
        "sources": snapshot["sources"],
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    (ROOT / "library-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    for name, files in (("ALZ", alz), ("AZURE-POLICY", builtin)):
        (ROOT / f"LICENSE-{name}").write_bytes(files["LICENSE"])
    print(f"Locked {len(used)} policy assets and {len(names)} assignments.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locked", action="store_true", help="Restore exact locked commits and hash")
    parser.add_argument("--if-missing", action="store_true", help="Reuse a matching local snapshot")
    args = parser.parse_args()
    if args.if_missing and not args.locked:
        parser.error("--if-missing requires --locked")
    refresh(locked=args.locked, if_missing=args.if_missing)
