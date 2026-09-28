"""Discover XRDs, Compositions and optional docs.yaml sidecars on disk."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

XRD_KIND = "CompositeResourceDefinition"
COMPOSITION_KIND = "Composition"


def repo_root() -> Path:
    """Return the repository root.

    ``mkdocs-gen-files`` runs scripts with the working directory set to the
    ``mkdocs.yml`` directory, so paths are resolved from this file's location
    rather than from ``Path.cwd()``.
    """
    # xrdoc/load.py -> xrdoc -> scripts -> docs -> apps -> <repo root>
    return Path(__file__).resolve().parents[4]


def _load_documents(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [doc for doc in yaml.safe_load_all(handle) if isinstance(doc, dict)]


def _first_of_kind(path: Path, kind: str) -> Optional[Dict[str, Any]]:
    for doc in _load_documents(path):
        if doc.get("kind") == kind:
            return doc
    return None


@dataclass
class CommonError:
    symptom: str
    cause: str = ""
    fix: str = ""


@dataclass
class Sidecar:
    """Hand-authored content that cannot be derived from any schema."""

    summary: str = ""
    stability: str = ""
    example_ref: str = ""
    example_lines: str = ""
    example_notes: str = ""
    composed_resources: List[Dict[str, Any]] = field(default_factory=list)
    common_errors: List[CommonError] = field(default_factory=list)

    @property
    def snippet(self) -> str:
        """The pymdownx.snippets include target, with optional line range."""
        if not self.example_ref:
            return ""
        if not self.example_lines:
            return self.example_ref
        start, _, end = self.example_lines.partition("-")
        return "{0}:{1}:{2}".format(self.example_ref, start.strip(), end.strip())

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Sidecar":
        if not data:
            return cls()
        errors = []
        for entry in data.get("commonErrors") or []:
            errors.append(
                CommonError(
                    symptom=str(entry.get("symptom", "")).strip(),
                    cause=str(entry.get("cause", "")).strip(),
                    fix=str(entry.get("fix", "")).strip(),
                )
            )
        return cls(
            summary=str(data.get("summary", "")).strip(),
            stability=str(data.get("stability", "")).strip(),
            example_ref=str(data.get("exampleRef", "")).strip(),
            example_lines=str(data.get("exampleLines", "")).strip(),
            example_notes=str(data.get("exampleNotes", "")).strip(),
            composed_resources=list(data.get("composedResources") or []),
            common_errors=errors,
        )


@dataclass
class CompositionDoc:
    """Everything known about a single composition directory."""

    directory: Path
    xrd: Dict[str, Any]
    composition: Optional[Dict[str, Any]]
    sidecar: Sidecar
    root: Path

    @property
    def spec(self) -> Dict[str, Any]:
        return self.xrd.get("spec") or {}

    @property
    def kind(self) -> str:
        return (self.spec.get("names") or {}).get("kind", "Unknown")

    @property
    def plural(self) -> str:
        return (self.spec.get("names") or {}).get("plural", "")

    @property
    def group(self) -> str:
        return self.spec.get("group", "")

    @property
    def scope(self) -> str:
        return self.spec.get("scope", "")

    @property
    def slug(self) -> str:
        return self.kind.lower()

    @property
    def cloud(self) -> str:
        """The provider directory name, e.g. ``azure``."""
        return self.directory.parent.name

    @property
    def versions(self) -> List[Dict[str, Any]]:
        return list(self.spec.get("versions") or [])

    @property
    def referenceable_version(self) -> Dict[str, Any]:
        """The version documentation is generated from.

        Prefers the ``referenceable`` version, since that is the one
        Compositions bind to; falls back to the first served version.
        """
        for version in self.versions:
            if version.get("referenceable"):
                return version
        for version in self.versions:
            if version.get("served"):
                return version
        return self.versions[0] if self.versions else {}

    @property
    def schema(self) -> Dict[str, Any]:
        version = self.referenceable_version
        return ((version.get("schema") or {}).get("openAPIV3Schema")) or {}

    @property
    def spec_schema(self) -> Dict[str, Any]:
        return (self.schema.get("properties") or {}).get("spec") or {}

    @property
    def status_schema(self) -> Dict[str, Any]:
        return (self.schema.get("properties") or {}).get("status") or {}

    def relative(self, path: Path) -> str:
        """Render a path relative to the repository root, for display."""
        return path.relative_to(self.root).as_posix()

    @property
    def xrd_path(self) -> str:
        return self.relative(self.directory / "xrd.yaml")

    @property
    def composition_path(self) -> str:
        return self.relative(self.directory / "composition.yaml")


def load_compositions(root: Optional[Path] = None) -> List[CompositionDoc]:
    """Load every composition under ``compositions/``, sorted by kind."""
    root = root or repo_root()
    docs: List[CompositionDoc] = []

    for xrd_path in sorted((root / "compositions").rglob("xrd.yaml")):
        xrd = _first_of_kind(xrd_path, XRD_KIND)
        if xrd is None:
            continue

        directory = xrd_path.parent

        composition_path = directory / "composition.yaml"
        composition = None
        if composition_path.is_file():
            composition = _first_of_kind(composition_path, COMPOSITION_KIND)

        sidecar_path = directory / "docs.yaml"
        sidecar_data = None
        if sidecar_path.is_file():
            documents = _load_documents(sidecar_path)
            sidecar_data = documents[0] if documents else None

        docs.append(
            CompositionDoc(
                directory=directory,
                xrd=xrd,
                composition=composition,
                sidecar=Sidecar.from_dict(sidecar_data),
                root=root,
            )
        )

    return sorted(docs, key=lambda doc: (doc.cloud, doc.kind))
