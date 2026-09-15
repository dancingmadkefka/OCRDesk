"""Role resolution: class map first, content heuristics as a backstop.

See docs/grader-plan.md section 3. `roles.yaml` covers only classes that
recur with consistent meaning across the real corpus; everything else falls
through to the content heuristics below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from . import fintoken
from .ir import Role

_ROLE_ORDER: tuple[Role, ...] = (
    "total_value",
    "numeric_value",
    "section_header",
    "label",
    "value",
    "handwritten",
    "monospace",
    "header",
    "spacer",
)

#: Alignment hints called out in roles.yaml's trailing comment. These are not
#: a Role; exposed for any future presentation tie-breaker (a documented
#: no-op hook per docs/grader-plan.md scope/assumptions).
ALIGNMENT_HINT_CLASSES: frozenset[str] = frozenset({
    "text-right", "right", "right-align", "right-aligned", "pull-right",
    "text-center", "center-text", "center",
})

_TOTAL_KEYWORD_RE = re.compile(
    r"\btotal\b|\bgesamt\b|\bsumme\b|\bsaldo\b|\bbetrag\b|\bnet pay\b|\bbalance\b|\bamount due\b",
    re.I,
)

# Pure-digit-with-separators fallback for is_numeric, layered on top of
# fintoken's amount/percent/date regexes (see _looks_numeric).
_PURE_DIGIT_RE = re.compile(r"^[+-]?\d[\d.,'  ]*$")


@dataclass(frozen=True)
class RoleMap:
    """Flattened class -> Role lookup, package defaults merged with an
    optional per-corpus override (corpus entries win on shared class names).
    """

    class_to_role: Mapping[str, Role] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleContext:
    """Context resolve_role needs beyond the bare element (tag/classes/text).

    `role_map` travels inside ctx rather than as its own positional
    parameter so resolve_role keeps the 4-argument shape named in
    docs/grader-plan.md ("resolve_role(tag, classes, text, ctx) -> Role").

    `in_thead_first_row` covers the "first <tr> of <thead>" half of the
    header heuristic (the "<th>" half needs no context: it's the tag itself).
    """

    role_map: RoleMap
    in_thead_first_row: bool = False


def _normalize_entry(entry: Any) -> tuple[list[str], dict[str, Any]]:
    """roles.yaml entries are either a bare class list or {classes: [...], ...}."""
    if isinstance(entry, dict):
        classes = list(entry.get("classes", []))
        extra = {k: v for k, v in entry.items() if k != "classes"}
        return classes, extra
    return list(entry or []), {}


def _load_yaml_classes(path: Path) -> dict[str, Role]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    class_to_role: dict[str, Role] = {}
    for role_name in _ROLE_ORDER:
        if role_name not in data:
            continue
        classes, _extra = _normalize_entry(data[role_name])
        for cls in classes:
            class_to_role[cls] = role_name  # type: ignore[assignment]
    return class_to_role


def load_role_map(corpus_dir: Path | None = None) -> RoleMap:
    """Load the package roles.yaml, merged with an optional `<corpus>/roles.yaml`.

    Corpus entries override the package default for any class name that
    appears in both; classes unique to either side are kept.
    """
    package_path = Path(__file__).with_name("roles.yaml")
    merged = _load_yaml_classes(package_path)
    if corpus_dir is not None:
        override_path = Path(corpus_dir) / "roles.yaml"
        if override_path.is_file():
            merged.update(_load_yaml_classes(override_path))
    return RoleMap(class_to_role=merged)


def looks_numeric(text: str) -> bool:
    """is_numeric heuristic: amount/percent/date regex match, or pure digits
    with separators (docs/grader-plan.md section 3).

    Public (not `_`-prefixed): tables.py reuses this exact check so a cell's
    `Cell.is_numeric` flag and its resolved Role agree with each other.
    """
    stripped = text.strip()
    if not stripped:
        return False
    tokens = fintoken.extract_tokens(stripped)
    if any(tok.type in ("amount", "percent", "date") for tok in tokens):
        return True
    return bool(_PURE_DIGIT_RE.match(stripped)) and any(ch.isdigit() for ch in stripped)


def resolve_role(
    tag: str,
    classes: Sequence[str] | None,
    text: str,
    ctx: RoleContext,
) -> Role:
    """Resolve one element to a Role: class map first, then content heuristics.

    The content-heuristic backstop only fires when no class matched. It
    covers: <th>/thead-first-row -> header; a total-keyword match combined
    with numeric-looking text -> total_value; numeric-looking text alone ->
    numeric_value; a bare total-keyword match (no numbers) -> label (it is
    almost always the label beside the total, e.g. "Total:" or "Balance").
    The last-numeric-row fallback lives in tables.py, which has the
    row-order context resolve_role deliberately does not need.
    """
    class_list = list(classes or [])
    role_map = ctx.role_map
    matched_roles = [
        role_map.class_to_role[cls] for cls in class_list if cls in role_map.class_to_role
    ]
    if matched_roles:
        for role_name in _ROLE_ORDER:
            if role_name in matched_roles:
                return role_name

    if tag == "th" or ctx.in_thead_first_row:
        return "header"

    stripped = text.strip()
    is_total_keyword = bool(_TOTAL_KEYWORD_RE.search(stripped))
    is_numeric = looks_numeric(stripped)

    if is_total_keyword and is_numeric:
        return "total_value"
    if is_numeric:
        return "numeric_value"
    if is_total_keyword:
        return "label"
    return "other"
