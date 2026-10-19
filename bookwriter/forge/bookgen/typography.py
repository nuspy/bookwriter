"""Typographic setup derived from the project configuration.

Font files are looked up on the machine. The first family in the preference
list that is actually installed wins, so the same project renders on a
workstation with EB Garamond and on one that only has the Windows defaults.
Whatever gets used is reported, because font licensing is the author's problem
to settle before a commercial print run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm

from .config import FONT_STACKS, Config

SERIF = "BookSerif"
SANS = "BookSans"
SYMBOL = "BookSymbol"

# Arrows and similar signs are missing from several text faces; they are taken
# from whichever installed font has them.
SYMBOL_CANDIDATES = ["cambria.ttc", "DejaVuSans.ttf", "seguisym.ttf",
                     "arial.ttf", "LiberationSans-Regular.ttf"]

FONT_DIRS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
]


def _find_font_file(filename: str) -> Optional[Path]:
    for d in FONT_DIRS:
        if not d.exists():
            continue
        direct = d / filename
        if direct.exists():
            return direct
        try:
            for found in d.rglob(filename):
                return found
        except OSError:
            continue
    return None


def _resolve_family(kind: str, preferred: Optional[str]
                    ) -> Tuple[Optional[str], Dict[str, Path]]:
    """Return (family name, {style: file}) for the first available family."""
    stacks = FONT_STACKS[kind]
    if preferred:
        ordered = ([s for s in stacks if s[0].lower() == preferred.lower()]
                   + [s for s in stacks if s[0].lower() != preferred.lower()])
        # a family named in the config but unknown to us: try common file names
        if not any(s[0].lower() == preferred.lower() for s in stacks):
            guess = preferred.replace(" ", "")
            ordered = [(preferred, f"{guess}.ttf", f"{guess}-Bold.ttf",
                        f"{guess}-Italic.ttf", f"{guess}-BoldItalic.ttf")] \
                + list(stacks)
    else:
        ordered = list(stacks)

    for name, regular, bold, italic, bolditalic in ordered:
        r = _find_font_file(regular)
        if not r:
            continue
        files = {"": r}
        for style, fname in (("-Bold", bold), ("-Italic", italic),
                             ("-BoldItalic", bolditalic)):
            f = _find_font_file(fname)
            if f:
                files[style] = f
        return name, files
    return None, {}


@dataclass
class Theme:
    """Everything the renderers need to know about page and type."""
    page_w: float
    page_h: float
    margin_inner: float
    margin_outer: float
    margin_top: float
    margin_bottom: float
    bleed: float
    body_size: float
    leading: float
    indent: float
    hyphen_lang: Optional[str]
    justify: bool

    serif_name: str = "Times-Roman"
    sans_name: str = "Helvetica"
    serif_family: str = SERIF
    sans_family: str = SANS
    has_symbol: bool = False
    registered: bool = False
    font_report: List[str] = field(default_factory=list)

    # ink
    INK = HexColor("#111111")
    INK_SOFT = HexColor("#4A4A4A")
    GREY = HexColor("#6E6E6E")
    RULE = HexColor("#B4B4B4")
    RULE_LIGHT = HexColor("#D8D8D8")
    TABLE_HEAD = HexColor("#ECECEC")
    TABLE_ZEBRA = HexColor("#F7F7F7")

    @property
    def frame_w(self) -> float:
        return self.page_w - self.margin_inner - self.margin_outer

    @property
    def frame_h(self) -> float:
        return self.page_h - self.margin_top - self.margin_bottom


def theme_from_config(cfg: Config) -> Theme:
    p, t = cfg.page, cfg.typography
    return Theme(
        page_w=p.width_mm * mm,
        page_h=p.height_mm * mm,
        margin_inner=p.margin_inner_mm * mm,
        margin_outer=p.margin_outer_mm * mm,
        margin_top=p.margin_top_mm * mm,
        margin_bottom=p.margin_bottom_mm * mm,
        bleed=p.bleed_mm * mm,
        body_size=t.body_size,
        leading=t.leading,
        indent=t.first_line_indent_mm * mm,
        hyphen_lang=t.hyphenation,
        justify=t.justify,
    )


def register_fonts(theme: Theme, cfg: Config) -> Theme:
    """Register the TrueType families with ReportLab, with fallbacks."""
    if theme.registered:
        return theme

    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for kind, base, preferred in (("serif", SERIF, cfg.typography.serif),
                                  ("sans", SANS, cfg.typography.sans)):
        name, files = _resolve_family(kind, preferred)
        if not files:
            theme.font_report.append(
                f"{kind}: no TrueType family found, using the PDF base font "
                f"(not embedded — replace before a commercial print run)")
            continue
        for style, path in files.items():
            pdfmetrics.registerFont(TTFont(f"{base}{style}", str(path)))
        # missing styles fall back to the regular cut
        for style in ("-Bold", "-Italic", "-BoldItalic"):
            if style not in files:
                pdfmetrics.registerFont(TTFont(f"{base}{style}",
                                               str(files[""])))
        addMapping(base, 0, 0, base)
        addMapping(base, 1, 0, f"{base}-Bold")
        addMapping(base, 0, 1, f"{base}-Italic")
        addMapping(base, 1, 1, f"{base}-BoldItalic")
        if kind == "serif":
            theme.serif_name, theme.serif_family = name, base
        else:
            theme.sans_name, theme.sans_family = name, base
        theme.font_report.append(f"{kind}: {name} ({len(files)} styles)")

    for candidate in SYMBOL_CANDIDATES:
        path = _find_font_file(candidate)
        if path:
            try:
                pdfmetrics.registerFont(TTFont(SYMBOL, str(path),
                                               subfontIndex=0))
                theme.has_symbol = True
                break
            except Exception:
                continue

    # Without this every page declares Helvetica as its base font: nothing is
    # drawn with it, but preflight tools flag it as not embedded.
    if theme.serif_family == SERIF:
        from reportlab import rl_config
        rl_config.canvas_basefontname = SERIF

    theme.registered = True
    return theme
