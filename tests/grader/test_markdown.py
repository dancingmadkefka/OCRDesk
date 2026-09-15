"""Tests for ocrgrade.markdown (docs/grader-plan.md section 1)."""
from __future__ import annotations

from ocrgrade.markdown import to_html


# --- hint == "html" -----------------------------------------------------


def test_html_hint_returns_as_is():
    assert to_html("<div>hi</div>", "html") == "<div>hi</div>"


def test_html_hint_strips_outer_fence():
    raw = "```html\n<div>hi</div>\n```"
    assert to_html(raw, "html") == "<div>hi</div>"


def test_html_hint_normalizes_crlf():
    assert to_html("<div>a</div>\r\n<div>b</div>", "html") == "<div>a</div>\n<div>b</div>"


# --- hint == "plain" ------------------------------------------------------


def test_plain_hint_wraps_in_pre_and_escapes():
    assert to_html("a < b & c", "plain") == "<pre>a &lt; b &amp; c</pre>"


def test_plain_hint_handles_crlf():
    assert to_html("line1\r\nline2", "plain") == "<pre>line1\nline2</pre>"


# --- hint == "markdown": headings, paragraphs ------------------------------


def test_markdown_heading_levels():
    assert to_html("# Title", "markdown") == "<h1>Title</h1>"
    assert to_html("### Sub", "markdown") == "<h3>Sub</h3>"


def test_markdown_paragraph():
    assert to_html("Hello world.", "markdown") == "<p>Hello world.</p>"


def test_markdown_paragraph_joins_soft_line_breaks():
    result = to_html("line one\nline two", "markdown")
    assert result == "<p>line one line two</p>"


def test_markdown_blank_line_separates_paragraphs():
    result = to_html("First para.\n\nSecond para.", "markdown")
    assert result == "<p>First para.</p>\n<p>Second para.</p>"


def test_markdown_escapes_content():
    result = to_html("Price < 5 & > 3", "markdown")
    assert "&lt;" in result and "&amp;" in result and "&gt;" in result


# --- hint == "markdown": pipe tables ---------------------------------------


def test_markdown_pipe_table_basic():
    raw = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    result = to_html(raw, "markdown")
    assert result == "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"


def test_markdown_pipe_table_with_alignment_row():
    raw = "| Name | Amount |\n|:---|---:|\n| Milk | 2.00 |\n| Oats | 1.19 |\n"
    result = to_html(raw, "markdown")
    assert result.startswith("<table><tr><td>Name</td><td>Amount</td></tr>")
    assert "<td>Milk</td><td>2.00</td>" in result
    assert "<td>Oats</td><td>1.19</td>" in result


def test_markdown_pipe_table_without_outer_pipes():
    raw = "A | B\n---|---\n1 | 2\n"
    result = to_html(raw, "markdown")
    assert result == "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"


# --- required dedicated case: CRLF pipe table --------------------------------


def test_markdown_pipe_table_with_crlf_input():
    raw = "| A | B |\r\n|---|---|\r\n| 1 | 2 |\r\n| 3 | 4 |\r\n"
    result = to_html(raw, "markdown")
    assert result == (
        "<table><tr><td>A</td><td>B</td></tr>"
        "<tr><td>1</td><td>2</td></tr>"
        "<tr><td>3</td><td>4</td></tr></table>"
    )


def test_markdown_mixed_content_with_crlf():
    raw = "# Title\r\n\r\n| A | B |\r\n|---|---|\r\n| 1 | 2 |\r\n\r\nOutro line.\r\n"
    result = to_html(raw, "markdown")
    assert result == (
        "<h1>Title</h1>\n"
        "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>\n"
        "<p>Outro line.</p>"
    )


# --- embedded raw HTML tables pass through untouched -------------------------


def test_markdown_embedded_raw_html_table_untouched():
    raw = "Intro\n\n<table><tr><td>raw</td></tr></table>\n\nOutro"
    result = to_html(raw, "markdown")
    assert result == "<p>Intro</p>\n<table><tr><td>raw</td></tr></table>\n<p>Outro</p>"


def test_markdown_fence_stripped_before_conversion():
    raw = "```markdown\n# Title\n\nBody text.\n```"
    result = to_html(raw, "markdown")
    assert result == "<h1>Title</h1>\n<p>Body text.</p>"


def test_unknown_hint_raises():
    import pytest

    with pytest.raises(ValueError):
        to_html("x", "bogus")  # type: ignore[arg-type]
