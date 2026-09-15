"""Shared intermediate representation for the ocrgrade grader.

Zero logic by design: every other ocrgrade module imports these shapes and
none of them may change the field sets during parallel implementation.
See docs/grader-plan.md section 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal[
    "numeric_value",
    "total_value",
    "section_header",
    "label",
    "value",
    "handwritten",
    "monospace",
    "header",
    "spacer",
    "other",
]

FinTokenType = Literal[
    "amount", "date", "percent", "iban", "vat_letter", "masked_card", "reference_id"
]

Tier = Literal["CATASTROPHIC", "REJECT", "PASS"]


@dataclass(frozen=True)
class CellRef:
    table_index: int
    row: int
    col: int


@dataclass(frozen=True)
class FinToken:
    type: FinTokenType
    raw: str
    canonical: str | None
    cents: int | None
    currency: str | None
    vat_letter: str | None
    attached: bool
    cell_ref: CellRef | None


@dataclass(frozen=True)
class Cell:
    table_index: int
    row: int
    col: int
    rowspan: int
    colspan: int
    is_span_origin: bool  # False on cells synthesized by span expansion
    text_raw: str
    text_norm: str
    role: Role
    is_numeric: bool
    is_header: bool
    tokens: list[FinToken]


@dataclass(frozen=True)
class Table:
    index: int
    n_rows: int
    n_cols: int
    cells: list[Cell]  # occupied grid, spans expanded
    outer_html: str  # exact serialized <table>...</table>, used by teds_adapter


@dataclass(frozen=True)
class LabelValuePair:
    label: str
    value: str
    source: Literal["table_row", "definition_list", "colon_pattern", "sibling_heuristic", "sidecar"]
    cell_ref: CellRef | None


@dataclass(frozen=True)
class LineItem:
    table_index: int
    row: int
    fields: dict[str, str]


@dataclass(frozen=True)
class Document:
    tables: list[Table]
    section_headers: list[str]
    label_value_pairs: list[LabelValuePair]
    line_items: list[LineItem]
    fin_tokens: list[FinToken]
    body_text_norm: str
    parse_ok: bool
    parse_error: str | None
    was_fragment: bool
    truncated: bool


@dataclass(frozen=True)
class CriticalField:
    role: str
    value: str
    cell_ref: CellRef | None
    expected_multiplicity: int = 1


@dataclass
class Sidecar:
    """Per-case annotation stored next to the GT as <stem>.meta.json (schema_version 1).

    Mutable on purpose: `annotate` writes it, `score` reads it.
    """

    schema_version: int
    case_id: str
    category: str
    category_secondary: str | None
    locale: str
    currency: str | None
    decimal_sep: str
    vat_letter_scheme: bool
    has_tables: bool
    required_sections: list[str]
    critical_fields: list[CriticalField]
    label_value_pairs: list[dict]
    line_item_schema: list[str]
    confirmed: bool = False
    notes: str = ""


@dataclass
class AssertionResult:
    id: str
    critical: bool
    passed: bool
    detail: str


@dataclass
class CaseResult:
    """Everything `score_document` knows about one case; serialized by report.py.

    `metrics` holds the flat metric dict defined in docs/grader-plan.md section 7.
    """

    case_id: str
    status: str  # "ok" | "error" | "skipped"
    input_form: str  # "html" | "markdown" | "plain"
    tier: Tier
    provisional: bool
    parse_ok: bool
    truncated: bool
    table_count_gt: int
    table_count_hyp: int
    structure_na: bool
    reading_order_na: bool
    metrics: dict[str, Any]
    assertions: list[AssertionResult]
    archival_safe: bool
    display_score: float
    runtime_seconds: float | None = None
    errors: list[str] = field(default_factory=list)
    category: str = "other"
