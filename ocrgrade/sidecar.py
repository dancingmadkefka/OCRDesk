"""Sidecar schema v1: load/save `<stem>.meta.json`, `annotate` derivation, CSV writer.

See docs/grader-plan.md section 2. The sidecar lives next to the GT file and is
never committed to the repo (see .gitignore) except for the synthetic files under
tests/grader/fixtures/, which are deliberately checked in as test data.

`derive()` needs a canonicalized `Document` to read roles/tokens off of. Scope A's
`canonicalize.build_document` / `roles.load_role_map` do not exist while this module
is developed in parallel, so both are accepted as injectable keyword parameters and
otherwise resolved via a lazy import at call time (see docs/grader-plan.md's Stage 1
contract for Scope C).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ocrgrade.ir import CellRef, CriticalField, Sidecar
from ocrgrade.roles import _TOTAL_KEYWORD_RE  # roles exists now; a module-level import keeps derive() free of lazy attribute lookups

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime dependency
    from ocrgrade.ir import Document
    from ocrgrade.inputs import CaseFiles

CATEGORY_ENUM = (
    "receipt",
    "invoice",
    "bank-statement",
    "card-statement",
    "tax",
    "form",
    "letter",
    "bill",
    "payslip",
    "other",
)

# Manifest `document_type` values (or close synonyms) that map directly onto the enum.
_MANIFEST_ALIASES = {
    "statement": "bank-statement",  # AIFA manifest uses 'statement' for bank/card/balancing statements
    "payslip": "payslip",
    "contract": "other",
    "receipt": "receipt",
    "invoice": "invoice",
    "bank_statement": "bank-statement",
    "bank-statement": "bank-statement",
    "bank statement": "bank-statement",
    "card_statement": "card-statement",
    "card-statement": "card-statement",
    "credit_card_statement": "card-statement",
    "tax": "tax",
    "tax_form": "tax",
    "form": "form",
    "letter": "letter",
    "bill": "bill",
    "utility_bill": "bill",
    "payslip": "payslip",
    "pay_slip": "payslip",
    "other": "other",
}

# Checked in priority order (most specific first) against the GT's normalized body
# text. This is a heuristic, not a classifier: `annotate` writes `confirmed: false`
# and a human is expected to correct `category` via the review CSV.
_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("bank-statement", r"\bbank statement\b|\bkontoauszug\b|\bsort code\b|\biban\b"),
    ("card-statement", r"\bcard statement\b|\bcredit card\b|\bstatement of account\b"),
    ("payslip", r"\bpayslip\b|\bpay slip\b|\bnet pay\b|\bpayroll\b|\bgehaltsabrechnung\b"),
    ("tax", r"\btax\b|\bvat return\b|\bp60\b|\bp45\b|\bsteuer\b|\brevenue commissioners\b"),
    ("invoice", r"\binvoice\b|\brechnung\b|\bfacture\b"),
    ("receipt", r"\breceipt\b|\bkassenbon\b|\bquittung\b"),
    ("form", r"\bapplication form\b|\bform no\b|\bantrag\b"),
    ("letter", r"\bdear (sir|madam|tenant|customer)\b|\byours sincerely\b|\byours faithfully\b"),
    ("bill", r"\bbill\b|\bamount due\b|\butility\b"),
)

_VAT_LETTER_RE = re.compile(r"\d(?:[.,]\d{2})?\s*[A-Za-z]\b")
_COMMA_DECIMAL_RE = re.compile(r"\d,\d{2}(?!\d)")
_DOT_DECIMAL_RE = re.compile(r"\d\.\d{2}(?!\d)")
_SUBTOTAL_RE = re.compile(r"sub[- ]?total", re.I)


def default_sidecar(case_id: str) -> Sidecar:
    """Schema defaults from docs/grader-plan.md section 2's table."""
    return Sidecar(
        schema_version=1,
        case_id=case_id,
        category="other",
        category_secondary=None,
        locale="OTHER",
        currency=None,
        decimal_sep=".",
        vat_letter_scheme=False,
        has_tables=False,
        required_sections=[],
        critical_fields=[],
        label_value_pairs=[],
        line_item_schema=[],
        confirmed=False,
        notes="",
    )


def sidecar_path_for_gt(gt_path: Path) -> Path:
    """The sidecar lives next to the GT, same stem, `.meta.json` extension."""
    return gt_path.with_name(f"{gt_path.stem}.meta.json")


def load(path: Path) -> Sidecar:
    """Load a sidecar from an explicit `.meta.json` path."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return _from_dict(data)


def save(sidecar: Sidecar, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_dict(sidecar), indent=2, ensure_ascii=False), encoding="utf-8")


def load_or_default(case_id: str, corpus_dir_or_case_files: "Path | CaseFiles") -> Sidecar:
    """Load the case's sidecar if one exists, else return schema defaults.

    Accepts either:
      - an object with a `.sidecar_path` attribute (a `CaseFiles`, as produced by
        `inputs.discover_corpus` / `inputs.discover_ocrdesk_corpus`) - the natural
        call shape from `cli.py`'s `score` pipeline, which already has one; or
      - a bare directory `Path` holding the case's own files (GT + optional
        sidecar) - used by `fixtures-check`, where the sidecar is committed as
        `sidecar.meta.json` rather than named after the GT's stem. Every
        `*.meta.json` file directly inside the directory is a candidate; the
        first match (sorted) wins.
    """
    sidecar_path = getattr(corpus_dir_or_case_files, "sidecar_path", None)
    if sidecar_path is not None:
        if sidecar_path.is_file():
            return load(sidecar_path)
        return default_sidecar(case_id)

    directory = corpus_dir_or_case_files
    assert isinstance(directory, Path), "expected a CaseFiles-like object or a Path"
    candidates = sorted(directory.glob("*.meta.json")) if directory.is_dir() else []
    if candidates:
        return load(candidates[0])
    return default_sidecar(case_id)


def derive(
    case_id: str,
    gt_html: str,
    manifest_entry: dict[str, Any] | None = None,
    *,
    build_document=None,
    role_map=None,
) -> Sidecar:
    """Derive a schema-v1 sidecar from GT html plus an optional manifest entry.

    Per docs/grader-plan.md section 2. `confirmed` is always `False`: a human
    reviews and confirms via `annotations_review.csv` before `score` treats the
    sidecar as authoritative (`provisional = not sidecar.confirmed`).
    """
    if build_document is None:
        from ocrgrade import canonicalize as _canonicalize

        build_document = _canonicalize.build_document
    if role_map is None:
        from ocrgrade import roles as _roles

        role_map = _roles.load_role_map(None)

    manifest_entry = manifest_entry or {}
    document: "Document" = build_document(gt_html, role_map, None)

    required_sections = list(dict.fromkeys(document.section_headers))
    has_tables = bool(manifest_entry["has_tables"]) if "has_tables" in manifest_entry else bool(document.tables)
    currency = _infer_currency(document)
    locale = _infer_locale(document, manifest_entry)
    decimal_sep = _infer_decimal_sep(document)
    vat_letter_scheme = _infer_vat_letter_scheme(document)
    category, category_secondary = _infer_category(document, manifest_entry)
    critical_fields = _build_critical_fields(document)
    label_value_pairs = [_lvp_to_dict(p) for p in document.label_value_pairs]
    line_item_schema = _build_line_item_schema(document)

    return Sidecar(
        schema_version=1,
        case_id=case_id,
        category=category,
        category_secondary=category_secondary,
        locale=locale,
        currency=currency,
        decimal_sep=decimal_sep,
        vat_letter_scheme=vat_letter_scheme,
        has_tables=has_tables,
        required_sections=required_sections,
        critical_fields=critical_fields,
        label_value_pairs=label_value_pairs,
        line_item_schema=line_item_schema,
        confirmed=False,
        notes="",
    )


def write_review_csv(sidecars: Iterable[Sidecar], path: Path) -> None:
    """Write `annotations_review.csv` at the corpus root (section 2 column list)."""
    columns = [
        "case_id",
        "category",
        "category_secondary",
        "locale",
        "currency",
        "decimal_sep",
        "vat_letter_scheme",
        "n_critical_fields",
        "n_required_sections",
        "n_label_value_pairs",
        "confirmed",
        "notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for sc in sidecars:
            writer.writerow(
                [
                    sc.case_id,
                    sc.category,
                    sc.category_secondary or "",
                    sc.locale,
                    sc.currency or "",
                    sc.decimal_sep,
                    sc.vat_letter_scheme,
                    len(sc.critical_fields),
                    len(sc.required_sections),
                    len(sc.label_value_pairs),
                    sc.confirmed,
                    sc.notes,
                ]
            )


# --- derivation helpers -----------------------------------------------------


def _infer_currency(document: "Document") -> str | None:
    for token in document.fin_tokens:
        if token.currency:
            return token.currency
    return None


def _infer_locale(document: "Document", manifest_entry: dict[str, Any]) -> str:
    for token in document.fin_tokens:
        if token.type == "iban" and token.canonical and len(token.canonical) >= 2:
            return token.canonical[:2].upper()
    language = manifest_entry.get("language")
    if language:
        return str(language).upper()
    return "OTHER"


def _infer_decimal_sep(document: "Document") -> str:
    comma = 0
    dot = 0
    for token in document.fin_tokens:
        if token.type != "amount":
            continue
        raw = token.raw or ""
        if _COMMA_DECIMAL_RE.search(raw):
            comma += 1
        elif _DOT_DECIMAL_RE.search(raw):
            dot += 1
    if comma > dot:
        return ","
    return "."


def _infer_vat_letter_scheme(document: "Document") -> bool:
    return any(token.vat_letter for token in document.fin_tokens)


def _normalize_manifest_category(manifest_entry: dict[str, Any]) -> str | None:
    raw = manifest_entry.get("document_type")
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _MANIFEST_ALIASES.get(key)


def _infer_category(document: "Document", manifest_entry: dict[str, Any]) -> tuple[str, str | None]:
    """The manifest `document_type` (curated per case) is the prior and wins when present;
    the keyword regex over the GT text only fills in when the manifest says nothing.
    Both are heuristics -- `annotate` always writes `confirmed: false` and expects human review.
    """
    prior = _normalize_manifest_category(manifest_entry)
    if prior:
        return prior, None
    text = document.body_text_norm or ""
    for category, pattern in _CATEGORY_KEYWORDS:
        if re.search(pattern, text, re.I):
            return category, None
    return "other", None


def _build_critical_fields(document: "Document") -> list[CriticalField]:
    """Critical fields = total cells in tables (role total_value with an amount) plus totals that
    live outside tables as label-value pairs ("Total: EUR66.71"). Each carries the label it sits
    beside so A1 can align it by meaning rather than by grid position."""
    amount_counts: dict[str, int] = {}
    for token in document.fin_tokens:
        if token.type == "amount" and token.canonical:
            amount_counts[token.canonical] = amount_counts.get(token.canonical, 0) + 1

    fields: list[CriticalField] = []
    seen_values: set[str] = set()
    for table in document.tables:
        for cell in table.cells:
            if cell.role != "total_value" or not cell.is_span_origin:
                continue
            value = _amount_value_for_cell(cell)
            if not value:
                continue  # a total cell with no amount (empty or label-only) is not a critical field
            labels = [
                c.text_norm.strip() for c in table.cells
                if c.row == cell.row and c.is_span_origin and c.col != cell.col
                and c.text_norm.strip() and not c.is_numeric
            ]
            label = min(labels, key=lambda t: (len(t.split()), len(t))) if labels else ""
            role = "subtotal" if _SUBTOTAL_RE.search(cell.text_norm + " " + label) else "grand_total"
            multiplicity = amount_counts.get(value, 1) if value else 1
            fields.append(CriticalField(role=role, value=value, cell_ref=CellRef(cell.table_index, cell.row, cell.col),
                                        expected_multiplicity=max(1, multiplicity), label=" ".join(label.split()[:12])))
            seen_values.add(value)

    for pair in document.label_value_pairs:
        if not _TOTAL_KEYWORD_RE.search(pair.label):
            continue
        amounts = [t for t in _extract_pair_tokens(pair.value) if t.type == "amount" and t.canonical]
        if len(amounts) != 1 or amounts[0].canonical in seen_values:
            continue
        value = amounts[0].canonical
        role = "subtotal" if _SUBTOTAL_RE.search(pair.label) else "grand_total"
        fields.append(CriticalField(role=role, value=value, cell_ref=None,
                                    expected_multiplicity=max(1, amount_counts.get(value, 1)),
                                    label=" ".join(pair.label.split()[:12])))
        seen_values.add(value)
    return fields


def _extract_pair_tokens(text: str):
    from ocrgrade.fintoken import extract_tokens

    return extract_tokens(text)


def _amount_value_for_cell(cell) -> str:
    """Canonical amount of a total cell, or '' when the cell holds no amount token."""
    for token in cell.tokens:
        if token.type == "amount" and token.canonical:
            return token.canonical
    return ""


def _lvp_to_dict(pair) -> dict[str, Any]:
    return {
        "label": pair.label,
        "value": pair.value,
        "source": pair.source,
        "cell_ref": asdict(pair.cell_ref) if pair.cell_ref is not None else None,
    }


def _build_line_item_schema(document: "Document") -> list[str]:
    by_table: dict[int, list] = {}
    for item in document.line_items:
        by_table.setdefault(item.table_index, []).append(item)

    best_table_index = None
    best_count = 0
    for table_index, items in by_table.items():
        if len(items) >= 3 and len(items) > best_count:
            best_table_index = table_index
            best_count = len(items)
    if best_table_index is None:
        return []
    return list(by_table[best_table_index][0].fields.keys())


# --- JSON (de)serialization --------------------------------------------------


def _to_dict(sidecar: Sidecar) -> dict[str, Any]:
    return asdict(sidecar)


def _from_dict(data: dict[str, Any]) -> Sidecar:
    critical_fields = [
        CriticalField(
            role=cf["role"],
            value=cf["value"],
            cell_ref=CellRef(**cf["cell_ref"]) if cf.get("cell_ref") else None,
            expected_multiplicity=cf.get("expected_multiplicity", 1),
            label=cf.get("label", ""),
        )
        for cf in data.get("critical_fields", [])
    ]
    return Sidecar(
        schema_version=data.get("schema_version", 1),
        case_id=data["case_id"],
        category=data.get("category", "other"),
        category_secondary=data.get("category_secondary"),
        locale=data.get("locale", "OTHER"),
        currency=data.get("currency"),
        decimal_sep=data.get("decimal_sep", "."),
        vat_letter_scheme=data.get("vat_letter_scheme", False),
        has_tables=data.get("has_tables", False),
        required_sections=list(data.get("required_sections", [])),
        critical_fields=critical_fields,
        label_value_pairs=list(data.get("label_value_pairs", [])),
        line_item_schema=list(data.get("line_item_schema", [])),
        confirmed=data.get("confirmed", False),
        notes=data.get("notes", ""),
    )
