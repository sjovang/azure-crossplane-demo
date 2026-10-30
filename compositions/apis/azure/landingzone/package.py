"""Stage only the generated manifests and release provenance for Flux OCI."""

import json
import shutil
from pathlib import Path

import generate


ROOT = Path(__file__).resolve().parent
DIST = ROOT.parents[2] / ".artifacts/landingzone"
FILES = {
    "definition.yaml": ROOT / "definition.yaml",
    "composition.yaml": ROOT / ".build/composition.yaml",
    "library.json": ROOT / ".build/library.json",
    "profile.json": ROOT / ".build/profile.json",
    "library-lock.json": ROOT / "library-lock.json",
    "release.json": ROOT / "release.json",
    "LICENSE-ALZ": ROOT / "LICENSE-ALZ",
    "LICENSE-AZURE-POLICY": ROOT / "LICENSE-AZURE-POLICY",
}


def package():
    for name, content in generate.generate().items():
        path = (ROOT if name == "xrd-patch.yaml" else ROOT / ".build") / name
        if path.read_text() != content:
            raise ValueError(f"{path} is stale; run make landingzone-artifact")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for name, source in FILES.items():
        shutil.copyfile(source, DIST / name)
    (DIST / "kustomization.yaml").write_text(
        "---\napiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\nresources:\n"
        "  - definition.yaml\n  - composition.yaml\n"
    )
    release = json.loads((ROOT / "release.json").read_text())["alz"]
    print(f"ALZ {release} artifact: {DIST}")


if __name__ == "__main__":
    package()
