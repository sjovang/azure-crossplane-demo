"""Render a CompositionDoc into a Markdown reference page."""

import json
from pathlib import Path
from typing import Any, List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .load import CompositionDoc
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
    """Flags shown next to the field name.

    ``immutable`` gets its own styled badge rather than joining the
    parenthetical list: setting it wrong means deleting and recreating the
    resource, so it needs to be visible when skimming the table.
    """
    out = ""
    if row.immutable:
        out += ' <span class="field-flag field-flag--immutable">immutable</span>'

    marks = []
    if row.nullable:
        marks.append("nullable")
    if row.preserve_unknown:
        marks.append("unvalidated")
    if marks:
        out += " <small>({0})</small>".format(", ".join(marks))
    return out


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
    spec_rows, _ = walk_fields(doc.spec_schema, "spec")
    status_rows, _ = walk_fields(doc.status_schema, "status")

    template = _environment().get_template("reference.md.j2")
    return template.render(
        doc=doc,
        sidecar=doc.sidecar,
        spec_rows=spec_rows,
        status_rows=status_rows,
        has_immutable=any(row.immutable for row in spec_rows),
        version_list=_version_list(doc),
        indent=_indent,
        badges=_badges,
        describe=_describe,
        default_cell=_default_cell,
        source_url=_source_url,
    )


def render_index(docs: List[CompositionDoc]) -> str:
    """Render the reference section landing page."""
    lines = [
        "---",
        "icon: lucide/boxes",
        "hide:",
        "  - toc",
        "---",
        "",
        "# Composition reference",
        "",
        "Every platform API available in this cluster. These are generated",
        "directly from the `CompositeResourceDefinition` manifests in",
        "`compositions/`, so they always match what is deployed.",
        "",
        "| Composition | Summary |",
        "| --- | --- |",
    ]
    for doc in docs:
        summary = doc.sidecar.summary.replace("\n", " ").strip() or "—"
        lines.append(
            "| [{0}]({1}.md) | {2} |".format(
                doc.kind, doc.slug, summary
            )
        )
    lines.append("")
    return "\n".join(lines)
