"""Derive the resources a Composition creates.

The three pipeline functions used in this repository store composed resources
in three different shapes, and one of them is not machine-readable:

``function-patch-and-transform``
    ``input.resources[].{name, base.apiVersion, base.kind}`` - fully derivable.
``function-kro``
    ``input.resources[].{id, template|externalRef .apiVersion/.kind,
    includeWhen}`` - fully derivable, different key names.
``function-go-templating``
    Resources live inside an inline Go template string and are emitted
    conditionally, so they cannot be derived. These compositions declare their
    composed resources in the ``docs.yaml`` sidecar instead.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

PATCH_AND_TRANSFORM = "function-patch-and-transform"
KRO = "function-kro"
GO_TEMPLATING = "function-go-templating"

# Steps that orchestrate rather than compose; listed in the pipeline but never
# rendered as composed resources.
UTILITY_FUNCTIONS = ("function-auto-ready",)


@dataclass
class ComposedResource:
    name: str
    kind: str
    api_version: str
    note: str = ""
    condition: str = ""
    external: bool = False

    @property
    def provider_group(self) -> str:
        return self.api_version.split("/")[0] if self.api_version else ""


@dataclass
class PipelineStep:
    step: str
    function: str
    resources: List[ComposedResource]
    derivable: bool = True

    @property
    def is_utility(self) -> bool:
        return self.function in UTILITY_FUNCTIONS


def _from_patch_and_transform(step: Dict[str, Any]) -> List[ComposedResource]:
    resources = []
    entries = ((step.get("input") or {}).get("resources")) or []
    for entry in entries:
        base = entry.get("base") or {}
        resources.append(
            ComposedResource(
                name=str(entry.get("name", "")),
                kind=str(base.get("kind", "")),
                api_version=str(base.get("apiVersion", "")),
            )
        )
    return resources


def _from_kro(step: Dict[str, Any]) -> List[ComposedResource]:
    resources = []
    entries = ((step.get("input") or {}).get("resources")) or []
    for entry in entries:
        template = entry.get("template") or {}
        external = entry.get("externalRef") or {}
        source = template or external
        conditions = entry.get("includeWhen") or []
        resources.append(
            ComposedResource(
                name=str(entry.get("id", "")),
                kind=str(source.get("kind", "")),
                api_version=str(source.get("apiVersion", "")),
                condition="; ".join(str(c) for c in conditions),
                external=bool(external) and not template,
            )
        )
    return resources


def _from_sidecar(declared: List[Dict[str, Any]]) -> List[ComposedResource]:
    resources = []
    for entry in declared or []:
        resources.append(
            ComposedResource(
                name=str(entry.get("name", entry.get("kind", ""))),
                kind=str(entry.get("kind", "")),
                api_version=str(entry.get("apiVersion", "")),
                note=str(entry.get("note", "")).strip(),
                condition=str(entry.get("condition", "")).strip(),
            )
        )
    return resources


def analyse(
    composition: Optional[Dict[str, Any]],
    declared: Optional[List[Dict[str, Any]]] = None,
) -> List[PipelineStep]:
    """Return one :class:`PipelineStep` per step in the composition pipeline.

    ``declared`` supplies sidecar-provided resources, used for steps whose
    resources cannot be derived from the manifest.
    """
    if not composition:
        return []

    steps: List[PipelineStep] = []
    pipeline = ((composition.get("spec") or {}).get("pipeline")) or []
    sidecar_used = False

    for entry in pipeline:
        function = str((entry.get("functionRef") or {}).get("name", ""))
        name = str(entry.get("step", ""))

        if function == PATCH_AND_TRANSFORM:
            steps.append(
                PipelineStep(name, function, _from_patch_and_transform(entry))
            )
        elif function == KRO:
            steps.append(PipelineStep(name, function, _from_kro(entry)))
        elif function == GO_TEMPLATING:
            resources = _from_sidecar(declared or [])
            sidecar_used = True
            steps.append(
                PipelineStep(name, function, resources, derivable=False)
            )
        else:
            steps.append(PipelineStep(name, function, []))

    # A sidecar may also augment a composition with no go-templating step.
    if declared and not sidecar_used:
        steps.append(
            PipelineStep("declared", "", _from_sidecar(declared), derivable=False)
        )

    return steps


def created_resources(steps: List[PipelineStep]) -> List[ComposedResource]:
    """Every resource the composition creates, excluding referenced ones."""
    resources = []
    for step in steps:
        for resource in step.resources:
            if not resource.external:
                resources.append(resource)
    return resources


def referenced_resources(steps: List[PipelineStep]) -> List[ComposedResource]:
    """Resources the composition reads but does not create."""
    resources = []
    for step in steps:
        for resource in step.resources:
            if resource.external:
                resources.append(resource)
    return resources
