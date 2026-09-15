"""Deterministic markdown -> HTML conversion (docs/grader-plan.md section 1).

Stdlib-only, no markdown library: the grammar we need (fences, headings,
paragraphs, GitHub pipe tables) is small and must stay perfectly
deterministic, so a hand-rolled line-based converter is more predictable
than pulling in a general-purpose markdown engine.
"""
from __future__ import annotations

import html
import re
from typing import Literal

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\n(.*?)\n```\s*$", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_SEP_CELL_RE = re.compile(r"^:?-{1,}:?$")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_fences(text: str) -> str:
    """Strip a single outer ```lang ... ``` fence wrapping the whole input.

    Only fires when the *entire* input is one fenced block (the common case
    of a model wrapping its whole answer in one fence); mixed fenced/plain
    content is left untouched rather than risking corruption.
    """
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text


def _escape(text: str) -> str:
    return html.escape(text)


def _split_row(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip())
    cells = [c.replace("\\|", "|").strip() for c in cells]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_separator_row(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _looks_like_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    header, sep = lines[i].strip(), lines[i + 1].strip()
    if "|" not in header or "|" not in sep:
        return False
    return _is_separator_row(sep)


def _convert_pipe_table(lines: list[str], i: int) -> tuple[str, int]:
    """Convert a GitHub pipe table (header + `---`/`:-:` separator row + body
    rows) starting at line `i` into `<table><tr><td>...` HTML. Returns the
    HTML and how many source lines were consumed.
    """
    header_cells = [_escape(c) for c in _split_row(lines[i])]
    j = i + 2  # skip the header row and the separator row
    body_rows: list[list[str]] = []
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        body_rows.append([_escape(c) for c in _split_row(lines[j])])
        j += 1

    rows_html = ["<tr>" + "".join(f"<td>{c}</td>" for c in header_cells) + "</tr>"]
    for row in body_rows:
        rows_html.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
    return "<table>" + "".join(rows_html) + "</table>", j - i


def _consume_raw_html_table(lines: list[str], i: int) -> tuple[str, int]:
    """Pass an embedded raw HTML <table>...</table> block through untouched."""
    block: list[str] = []
    j = i
    while j < len(lines):
        block.append(lines[j])
        if "</table>" in lines[j].lower():
            j += 1
            break
        j += 1
    return "\n".join(block), j - i


def _convert_markdown(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_escape(' '.join(paragraph))}</p>")
            paragraph.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if stripped.lower().startswith("<table"):
            flush_paragraph()
            block, consumed = _consume_raw_html_table(lines, i)
            out.append(block)
            i += consumed
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            out.append(f"<h{level}>{_escape(heading_match.group(2).strip())}</h{level}>")
            i += 1
            continue

        if _looks_like_table_start(lines, i):
            flush_paragraph()
            block, consumed = _convert_pipe_table(lines, i)
            out.append(block)
            i += consumed
            continue

        paragraph.append(stripped)
        i += 1

    flush_paragraph()
    return "\n".join(out)


def to_html(raw: str, hint: Literal["html", "markdown", "plain"]) -> str:
    """Convert `raw` hypothesis text to HTML per its declared `hint`.

    Always normalizes CRLF/CR to LF and strips one enclosing code fence
    first. "html" is then returned as-is; "markdown" converts headings,
    paragraphs and GitHub pipe tables to `<table><tr><td>` HTML while
    passing any embedded raw HTML `<table>` through untouched; "plain" is
    HTML-escaped and wrapped in a single `<pre>`.
    """
    text = _strip_fences(_normalize_newlines(raw))
    if hint == "html":
        return text
    if hint == "plain":
        return f"<pre>{_escape(text)}</pre>"
    if hint == "markdown":
        return _convert_markdown(text)
    raise ValueError(f"unknown markdown hint: {hint!r}")
