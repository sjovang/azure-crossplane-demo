from pathlib import Path
from typing import Any

import yaml


SUPPORTED_KINDS = {
    "XAppService",
    "XContainerApp",
    "XDatabase",
    "XResourceGroup",
    "XWebApplication",
}


def load_catalog(root: Path) -> str:
    entries: list[dict[str, Any]] = []

    for path in sorted(root.glob("**/xrd.yaml")):
        document = yaml.safe_load(path.read_text())
        kind = document["spec"]["names"]["kind"]
        if kind not in SUPPORTED_KINDS:
            continue

        schema = document["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
        entries.append(
            {
                "kind": kind,
                "apiVersion": (
                    f"{document['spec']['group']}/"
                    f"{document['spec']['versions'][0]['name']}"
                ),
                "scope": document["spec"]["scope"],
                "specSchema": schema["properties"]["spec"],
                "source": str(path.relative_to(root)),
            }
        )

    return yaml.safe_dump(entries, sort_keys=False)
