import re
from typing import Any

import yaml


TEAM_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RenderError(Exception):
    pass


def validate_team_name(team_name: str) -> str:
    if not TEAM_NAME.fullmatch(team_name) or len(team_name) > 40:
        raise RenderError(
            "Team name must be a lowercase DNS name of at most 40 characters."
        )
    if team_name.startswith("_"):
        raise RenderError("Team name cannot start with an underscore.")
    return team_name


def _documents(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    pattern = recommendation["pattern"]
    name = recommendation["name"]
    common = {
        "name": name,
        "image": recommendation["image"],
        "port": recommendation["port"],
        "location": recommendation["location"],
    }

    if pattern == "webapplication":
        spec = {
            **common,
            "appSize": recommendation["size"],
            "databaseSize": recommendation["databaseSize"],
        }
        if recommendation.get("entraIdAuth"):
            spec["entraIdAuth"] = {"enabled": True}
        return [
            {
                "apiVersion": "environments.platform.example.org/v1alpha1",
                "kind": "XWebApplication",
                "metadata": {"name": name},
                "spec": spec,
            }
        ]

    resource_group = {
        "apiVersion": "azure.platform.example.org/v1alpha1",
        "kind": "XResourceGroup",
        "metadata": {"name": name},
        "spec": {
            "name": name,
            "location": recommendation["location"],
            "tags": {"managed-by": "crossplane"},
        },
    }
    kind = "XAppService" if pattern == "appservice" else "XContainerApp"
    application = {
        "apiVersion": "azure.platform.example.org/v1alpha1",
        "kind": kind,
        "metadata": {"name": name},
        "spec": {
            **common,
            "resourceGroupRef": {"name": name},
            "size": recommendation["size"],
        },
    }
    return [resource_group, application]


def render_team(
    team_name: str,
    recommendation: dict[str, Any],
) -> dict[str, str]:
    team_name = validate_team_name(team_name)
    infrastructure = "---\n" + "---\n".join(
        yaml.safe_dump(document, sort_keys=False) for document in _documents(recommendation)
    )

    kustomization = yaml.safe_dump(
        {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "namespace": team_name,
            "resources": [
                "../_base",
                "catalog-info.yaml",
                "infrastructure.yaml",
            ],
        },
        sort_keys=False,
    )
    catalog_info = yaml.safe_dump_all(
        [
            {
                "apiVersion": "backstage.io/v1alpha1",
                "kind": "Group",
                "metadata": {
                    "name": team_name,
                    "description": f"Team created by the app hosting advisor.",
                },
                "spec": {"type": "team", "children": []},
            },
            {
                "apiVersion": "backstage.io/v1alpha1",
                "kind": "System",
                "metadata": {
                    "name": team_name,
                    "description": (
                        f"Azure resources provisioned for the {team_name} team."
                    ),
                },
                "spec": {"owner": f"group:{team_name}"},
            },
        ],
        sort_keys=False,
        explicit_start=True,
    )

    prefix = f"teams/{team_name}"
    return {
        f"{prefix}/kustomization.yaml": "---\n" + kustomization,
        f"{prefix}/catalog-info.yaml": catalog_info,
        f"{prefix}/infrastructure.yaml": infrastructure,
    }
