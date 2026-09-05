"""Unit tests for agents/build_legal.py — the self-contained mini-markdown renderer.

Pure functions (render, inline); no file I/O touched.
"""
from agents import build_legal as bl


# --------------------------------------------------------------------------- inline

def test_inline_escapes_html():
    assert bl.inline("a < b & c") == "a &lt; b &amp; c"


def test_inline_bold():
    assert bl.inline("this is **bold** text") == "this is <strong>bold</strong> text"


def test_inline_hard_break_sentinel():
    assert bl.inline("line one" + bl.HARD_BREAK + "line two") == "line one<br>line two"


def test_inline_strips_surrounding_whitespace():
    assert bl.inline("  hello  ") == "hello"


def test_inline_bold_does_not_break_escaping():
    # bold applied AFTER escaping, so tags inside stay escaped.
    assert bl.inline("**<x>**") == "<strong>&lt;x&gt;</strong>"


# --------------------------------------------------------------------------- render headings

def test_render_h1():
    out = bl.render("# Title")
    assert "<h1" in out
    assert ">Title</h1>" in out
    assert bl.H_CLASSES[1] in out


def test_render_h2_h3():
    assert "<h2" in bl.render("## Sub")
    assert "<h3" in bl.render("### Small")


# --------------------------------------------------------------------------- render bullets

def test_render_bullets_grouped_in_ul():
    out = bl.render("- one\n- two")
    assert out.count("<ul") == 1
    assert "<li>one</li>" in out
    assert "<li>two</li>" in out


def test_render_bullets_with_leading_whitespace():
    out = bl.render("  - indented")
    assert "<li>indented</li>" in out


# --------------------------------------------------------------------------- render paragraphs

def test_render_paragraph_joins_lines():
    out = bl.render("line one\nline two")
    assert '<p class=' in out
    assert "line one line two" in out


def test_render_blank_line_separates_paragraphs():
    out = bl.render("para one\n\npara two")
    assert out.count("<p ") == 2


# --------------------------------------------------------------------------- render rules

def test_render_horizontal_rule():
    out = bl.render("---")
    assert "<hr" in out


# --------------------------------------------------------------------------- render hard break

def test_render_hard_break_two_trailing_spaces():
    # a line ending in two spaces becomes a <br> at the join.
    out = bl.render("first  \nsecond")
    # lines join with a space, so the sentinel becomes "<br> " at the boundary.
    assert "first<br> second" in out


def test_render_bold_in_paragraph():
    out = bl.render("some **strong** words")
    assert "<strong>strong</strong>" in out


# --------------------------------------------------------------------------- render mixed / flush behaviour

def test_render_bullets_flush_before_heading():
    out = bl.render("- item\n# Heading")
    # ul closes before the heading opens.
    assert out.index("</ul>") < out.index("<h1")


def test_render_paragraph_flush_before_bullets():
    out = bl.render("intro text\n- bullet")
    assert out.index("</p>") < out.index("<ul")


def test_render_empty_string():
    assert bl.render("") == ""


def test_render_escapes_in_all_contexts():
    out = bl.render("# A & B\n\n- x < y\n\nplain & text")
    assert "A &amp; B" in out
    assert "x &lt; y" in out
    assert "plain &amp; text" in out
