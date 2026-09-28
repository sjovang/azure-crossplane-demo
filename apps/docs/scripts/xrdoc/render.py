"""Render a CompositionDoc into a Markdown reference page."""

import json
import re
from pathlib import Path
from typing import Any, List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .composed import (
    ComposedResource,
    created_resources,
    referenced_resources,
)
from .composed import analyse as analyse_pipeline
from .load import CompositionDoc
from .mermaid import render as render_mermaid
from .schema import FieldRow, walk_fields

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# Manifests live outside docs_dir, so links to them point at the repository
# rather than at a relative path MkDocs cannot resolve.
SOURCE_BASE = "https://github.com/sjovang/azure-crossplane-demo/blob/main"

# Non-breaking spaces, so nesting survives Markdown table rendering.
INDENT = "&nbsp;&nbsp;&nbsp;&nbsp;"


def _indent(depth: int) -> str:
    return INDENT * max(depth, 0)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return "`{0}`".format(value)
    return "`{0}`".format(json.dumps(value))


def _default_cell(row: FieldRow) -> str:
    if not row.has_default:
        return "—"
    if row.default is None:
        return "`null`"
    return _format_value(row.default)


def _badges(row: FieldRow) -> str:
    marks = []
    if row.nullable:
        marks.append("nullable")
    if row.preserve_unknown:
        marks.append("unvalidated")
    if not marks:
        return ""
    return " <small>({0})</small>".format(", ".join(marks))


def _describe(row: FieldRow) -> str:
    """Build the description cell: prose, enum values, then constraints."""
    parts = []
    if row.description:
        parts.append(row.description.replace("\n", " ").strip())
    if row.enum:
        parts.append(
            "One of: {0}.".format(
                ", ".join(_format_value(value) for value in row.enum)
            )
        )
    if row.constraints:
        parts.append("Constraints: {0}.".format(", ".join(row.constraints)))
    return _escape_cell(" ".join(parts)) or "—"


def _escape_cell(text: str) -> str:
    """Escape pipes so a value survives a Markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _humanise_condition(condition: str) -> str:
    """Turn a kro includeWhen expression into readable prose."""
    if not condition:
        return ""
    match = re.search(
        r"schema\.spec\.([A-Za-z0-9_.]+)\s*==\s*\"([^\"]+)\"", condition
    )
    if match:
        return "only when `spec.{0}` is `{1}`".format(
            match.group(1), match.group(2)
        )
    return condition.strip()


def _resource_note(resource: ComposedResource) -> str:
    parts = []
    if resource.note:
        parts.append(resource.note)
    condition = _humanise_condition(resource.condition)
    if condition:
        parts.append("Created {0}.".format(condition))
    if not parts and resource.api_version:
        parts.append("`{0}`".format(resource.api_version))
    return _escape_cell(" ".join(parts)) or "—"


def _source_url(path: str) -> str:
    """Link from a generated page back to the manifest in the repository."""
    return "{0}/{1}".format(SOURCE_BASE, path)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env


def _version_list(doc: CompositionDoc) -> str:
    labels = []
    for version in doc.versions:
        name = version.get("name", "")
        flags = []
        if version.get("referenceable"):
            flags.append("referenceable")
        if not version.get("served", True):
            flags.append("not served")
        label = "`{0}`".format(name)
        if flags:
            label = "{0} <small>({1})</small>".format(label, ", ".join(flags))
        labels.append(label)
    return ", ".join(labels) or "—"


def render_page(doc: CompositionDoc) -> str:
    """Render the full Markdown reference page for one composition."""
    spec_rows, spec_rules = walk_fields(doc.spec_schema, "spec")
    status_rows, _ = walk_fields(doc.status_schema, "status")

    steps = analyse_pipeline(doc.composition, doc.sidecar.composed_resources)
    created = created_resources(steps)
    referenced = referenced_resources(steps)

    template = _environment().get_template("reference.md.j2")
    return template.render(
        doc=doc,
        sidecar=doc.sidecar,
        spec_rows=spec_rows,
        spec_rules=spec_rules,
        status_rows=status_rows,
        created=created,
        referenced=referenced,
        diagram=render_mermaid(doc.kind, steps),
        version_list=_version_list(doc),
        indent=_indent,
        badges=_badges,
        describe=_describe,
        default_cell=_default_cell,
        resource_note=_resource_note,
        source_url=_source_url,
        escape_cell=_escape_cell,
    )


def render_index(docs: List[CompositionDoc]) -> str:
    """Render the reference section landing page."""
    lines = [
        "# Composition reference",
        "",
        "Every platform API available in this cluster. These are generated",
        "directly from the `CompositeResourceDefinition` manifests in",
        "`compositions/`, so they always match what is deployed.",
        "",
        "| Composition | Scope | Summary |",
        "| --- | --- | --- |",
    ]
    for doc in docs:
        summary = doc.sidecar.summary.replace("\n", " ").strip() or "—"
        lines.append(
            "| [{0}]({1}.md) | `{2}` | {3} |".format(
                doc.kind, doc.slug, doc.scope, summary
            )
        )
    lines.append("")
    return "\n".join(lines)
