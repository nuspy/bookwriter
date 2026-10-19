"""Inline markup conversion for the three output engines."""

import re

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL)
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"(?<!\")(https?://[^\s,)<]+)")

# Signs missing from most text faces; drawn from the symbol font instead.
FALLBACK_GLYPHS = "→←↔⇒⇐≤≥≠±×÷"


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def to_rl(text: str, theme=None) -> str:
    """Markup for ReportLab paragraphs."""
    out = _escape(text)
    out = _LINK.sub(r"\1", out)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    sans = getattr(theme, "sans_family", None)
    out = _CODE.sub(rf"<font face='{sans}'>\1</font>", out) if sans \
        else _CODE.sub(r"\1", out)
    if theme is not None and getattr(theme, "has_symbol", False):
        for g in FALLBACK_GLYPHS:
            if g in out:
                out = out.replace(g, f"<font face='BookSymbol'>{g}</font>")
    return out


def to_html(text: str, links: bool = True) -> str:
    """Markup for XHTML (EPUB)."""
    out = _escape(text)
    out = _LINK.sub(r'<a href="\2">\1</a>', out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    out = _CODE.sub(r"<code>\1</code>", out)
    if links:
        out = _URL.sub(r'<a href="\1">\1</a>', out)
    return out


def to_runs(text: str):
    """(text, bold, italic) tuples for python-docx."""
    tokens, pos = [], 0
    pattern = re.compile(
        r"\*\*(.+?)\*\*|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|`([^`]+)`",
        re.DOTALL)
    for m in pattern.finditer(text):
        if m.start() > pos:
            tokens.append((text[pos:m.start()], False, False))
        if m.group(1) is not None:
            tokens.append((m.group(1), True, False))
        elif m.group(2) is not None:
            tokens.append((m.group(2), False, True))
        else:
            tokens.append((m.group(3), False, False))
        pos = m.end()
    if pos < len(text):
        tokens.append((text[pos:], False, False))
    return [t for t in tokens if t[0]]


def plain(text: str) -> str:
    out = _LINK.sub(r"\1", text)
    out = _BOLD.sub(r"\1", out)
    out = _ITALIC.sub(r"\1", out)
    return _CODE.sub(r"\1", out)
