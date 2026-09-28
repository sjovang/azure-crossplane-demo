"""Recursive walker turning an openAPIV3Schema into flat, renderable rows.

Kubernetes-flavoured OpenAPI v3 has a few traps this module handles
explicitly:

* ``required`` is local to the enclosing object, never global.
* ``additionalProperties`` holding a schema means "map type"; such a node has
  no ``properties`` to iterate.
* ``nullable: true`` is an OpenAPI 3.0 flag most generic tooling drops.
* ``x-kubernetes-validations`` (CEL) attach to object nodes, not only leaves.
* Schemas are fully inlined, so there is no ``$ref`` to resolve.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

CONSTRAINT_KEYS: Sequence[Tuple[str, str]] = (
    ("minimum", "min"),
    ("maximum", "max"),
    ("minLength", "minLength"),
    ("maxLength", "maxLength"),
    ("minItems", "minItems"),
    ("maxItems", "maxItems"),
    ("pattern", "pattern"),
    ("format", "format"),
)


@dataclass
class CelRule:
    path: str
    rule: str
    message: str = ""


@dataclass
class FieldRow:
    path: str
    name: str
    type_name: str
    required: bool
    depth: int
    description: str = ""
    default: Any = None
    has_default: bool = False
    enum: Optional[List[Any]] = None
    constraints: List[str] = field(default_factory=list)
    nullable: bool = False
    preserve_unknown: bool = False
    immutable: bool = False

    @property
    def is_nested(self) -> bool:
        return self.depth > 0


def describe_type(schema: Dict[str, Any]) -> str:
    """Render a human-readable type name for a schema node."""
    if schema.get("x-kubernetes-int-or-string"):
        return "int or string"

    declared = schema.get("type")
    if declared is None:
        if "properties" in schema:
            declared = "object"
        elif "items" in schema:
            declared = "array"
        else:
            return "any"

    if declared == "array":
        return "[]{0}".format(describe_type(schema.get("items") or {}))

    if declared == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return "map[string]{0}".format(describe_type(additional))
        if additional is True:
            return "map[string]any"
        if not schema.get("properties"):
            if schema.get("x-kubernetes-preserve-unknown-fields"):
                return "object (unvalidated)"
        return "object"

    return str(declared)


def _constraints(schema: Dict[str, Any]) -> List[str]:
    parts: List[str] = []
    for key, label in CONSTRAINT_KEYS:
        if key in schema:
            parts.append("{0}: {1}".format(label, schema[key]))
    return parts


def _cel_rules(schema: Dict[str, Any], path: str, root_label: str) -> List[CelRule]:
    rules = []
    for entry in schema.get("x-kubernetes-validations") or []:
        if not isinstance(entry, dict) or "rule" not in entry:
            continue
        rules.append(
            CelRule(
                path=path or root_label,
                rule=str(entry["rule"]).strip(),
                message=str(entry.get("message", "")).strip(),
            )
        )
    return rules


def _is_immutable(schema: Dict[str, Any]) -> bool:
    """Detect the CEL transition rule that pins a field after creation.

    Immutability has no dedicated OpenAPI keyword: it is expressed as a
    ``self == oldSelf`` transition rule. The rule attaches to the field's own
    node, whether that is a scalar or an object, so a whole object can be
    immutable while its properties carry no rule of their own.

    The "immutable once set" variant, ``self == oldSelf || oldSelf == null``,
    counts too -- the field cannot be changed once it has a value.
    """
    for entry in schema.get("x-kubernetes-validations") or []:
        if not isinstance(entry, dict) or "rule" not in entry:
            continue
        for clause in str(entry["rule"]).split("||"):
            tokens = clause.replace("(", " ").replace(")", " ").split()
            if len(tokens) == 3 and tokens[1] == "==":
                if {tokens[0], tokens[2]} == {"self", "oldSelf"}:
                    return True
    return False


def walk(
    schema: Dict[str, Any],
    path: Tuple[str, ...] = (),
    depth: int = 0,
    required: FrozenSet[str] = frozenset(),
    rules: Optional[List[CelRule]] = None,
    root_label: str = "spec",
) -> Tuple[List[FieldRow], List[CelRule]]:
    """Flatten ``schema`` into ordered rows plus every CEL rule found.

    ``path`` is the dotted field path built so far; synthetic segments are
    used for array items (``[]``) and map values (``<key>``).
    """
    rows: List[FieldRow] = []
    if rules is None:
        rules = []

    if not isinstance(schema, dict):
        return rows, rules

    joined = ".".join(path).replace(".[]", "[]").replace(".<key>", "[key]")
    rules.extend(_cel_rules(schema, joined, root_label))

    # Array-item and map-value nodes carry no field of their own: the parent
    # already renders as []T or map[string]T. Skip the row but keep recursing
    # so children still get a correct dotted path.
    synthetic = bool(path) and path[-1] in ("[]", "<key>")

    if path and not synthetic:
        rows.append(
            FieldRow(
                path=joined,
                name=path[-1],
                type_name=describe_type(schema),
                required=path[-1] in required,
                depth=depth - 1,
                description=str(schema.get("description") or "").strip(),
                default=schema.get("default"),
                has_default="default" in schema,
                enum=list(schema["enum"]) if "enum" in schema else None,
                constraints=_constraints(schema),
                nullable=bool(schema.get("nullable")),
                preserve_unknown=bool(
                    schema.get("x-kubernetes-preserve-unknown-fields")
                ),
                immutable=_is_immutable(schema),
            )
        )

    child_required = frozenset(schema.get("required") or [])
    for name, child in (schema.get("properties") or {}).items():
        child_rows, rules = walk(
            child, path + (name,), depth + 1, child_required, rules, root_label
        )
        rows.extend(child_rows)

    items = schema.get("items")
    if isinstance(items, dict):
        item_rows, rules = walk(
            items, path + ("[]",), depth, frozenset(), rules, root_label
        )
        rows.extend(item_rows)

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict) and additional.get("properties"):
        map_rows, rules = walk(
            additional, path + ("<key>",), depth, frozenset(), rules, root_label
        )
        rows.extend(map_rows)

    return rows, rules


def walk_fields(
    schema: Dict[str, Any], root_label: str = "spec"
) -> Tuple[List[FieldRow], List[CelRule]]:
    """Walk a top-level ``spec`` or ``status`` schema."""
    return walk(schema, root_label=root_label)
