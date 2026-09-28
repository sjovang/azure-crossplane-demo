"""Render a Composition pipeline as a Mermaid flowchart.

Material for MkDocs renders ```mermaid fences natively via the
``pymdownx.superfences`` custom fence, so the output here is dropped straight
into the generated page.
"""

import re
from typing import List

from .composed import PipelineStep

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _node_id(prefix: str, value: str, index: int) -> str:
    cleaned = _SAFE.sub("_", value) or "n{0}".format(index)
    return "{0}_{1}".format(prefix, cleaned)


def _escape(text: str) -> str:
    """Quote a Mermaid node label safely."""
    return text.replace('"', "'").replace("\n", " ").strip()


def _condition_label(condition: str) -> str:
    """Shorten a kro includeWhen expression into a readable edge label."""
    match = re.search(r'==\s*"([^"]+)"', condition)
    if match:
        return "if {0}".format(match.group(1))
    return "conditional"


def render(kind: str, steps: List[PipelineStep]) -> str:
    """Return a fenced Mermaid diagram, or an empty string if there is none."""
    if not steps:
        return ""

    lines = ["```mermaid", "flowchart TD"]
    xr_id = _node_id("xr", kind, 0)
    lines.append('    {0}["{1}<br/>(composite)"]'.format(xr_id, _escape(kind)))

    previous = xr_id
    has_resources = False

    for index, step in enumerate(steps):
        if not step.resources and not step.is_utility and not step.step:
            continue

        step_id = _node_id("step", step.step, index)
        label = _escape(step.step)
        if step.function:
            label = "{0}<br/>{1}".format(label, _escape(step.function))
        lines.append('    {0}["{1}"]'.format(step_id, label))
        lines.append("    {0} --> {1}".format(previous, step_id))
        previous = step_id

        for offset, resource in enumerate(step.resources):
            has_resources = True
            resource_id = _node_id(
                "res", resource.name or resource.kind, offset
            )
            resource_label = _escape(resource.kind or resource.name)
            if resource.api_version:
                resource_label = "{0}<br/>{1}".format(
                    resource_label, _escape(resource.provider_group)
                )

            if resource.external:
                lines.append(
                    '    {0}[/"{1}"/]'.format(resource_id, resource_label)
                )
            else:
                lines.append('    {0}["{1}"]'.format(resource_id, resource_label))

            if resource.condition:
                lines.append(
                    "    {0} -->|{1}| {2}".format(
                        step_id, _condition_label(resource.condition), resource_id
                    )
                )
            else:
                lines.append("    {0} --> {1}".format(step_id, resource_id))

    if not has_resources:
        return ""

    lines.append("```")
    return "\n".join(lines)
