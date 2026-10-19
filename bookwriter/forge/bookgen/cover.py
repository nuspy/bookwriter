"""Cover preparation for print and for the ebook stores.

The cover artwork is the author's: this module never invents one. If the file
is missing the build stops with an explicit request, because a print cover has
requirements (resolution, aspect ratio, bleed) that must be settled with
whoever supplies the image, not patched afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import Config
from .typography import _find_font_file, _resolve_family

DPI = 300
MM = DPI / 25.4
EBOOK_HEIGHT = 2560          # KDP recommends 2560 on the long side
EBOOK_RATIO = 1.6


class CoverMissing(Exception):
    """Raised when the project has no artwork yet."""


@dataclass
class CoverReport:
    files: List[Path]
    source_px: Tuple[int, int]
    effective_dpi: float
    upscaled: bool
    warnings: List[str]


def _hex(color: str) -> Tuple[int, int, int]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _fonts(cfg: Config):
    _, serif = _resolve_family("serif", cfg.typography.serif)
    _, sans = _resolve_family("sans", cfg.typography.sans)
    title = serif.get("-Bold") or serif.get("")
    author = sans.get("") or title
    if title is None:
        title = author = _find_font_file("arial.ttf")
    return title, author


def _text_width(draw, text, font, tracking=0.0) -> float:
    return draw.textlength(text, font=font) + tracking * max(len(text) - 1, 0)


def _draw_tracked(draw, xy, text, font, fill, tracking=0.0, centre=True):
    x, y = xy
    total = _text_width(draw, text, font, tracking)
    if centre:
        x -= total / 2.0
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return total


def _fit_width(image: Image.Image, w: int) -> Image.Image:
    """Scale to full width without cropping the sides.

    Artwork usually carries text near the edges; a horizontal crop would
    mutilate it. Missing height is added as a band underneath.
    """
    h = int(round(w * image.height / image.width))
    up = image.resize((w, h), Image.LANCZOS)
    if w > image.width:
        up = up.filter(ImageFilter.UnsharpMask(radius=1.6, percent=68,
                                               threshold=3))
    return up


def _fit_cover(image: Image.Image, w: int, h: int) -> Image.Image:
    ratio_dst, ratio_src = w / h, image.width / image.height
    if ratio_src > ratio_dst:
        nh, nw = image.height, int(round(image.height * ratio_dst))
    else:
        nw, nh = image.width, int(round(image.width / ratio_dst))
    left, top = (image.width - nw) // 2, (image.height - nh) // 2
    out = image.crop((left, top, left + nw, top + nh)).resize((w, h),
                                                              Image.LANCZOS)
    if w > nw:
        out = out.filter(ImageFilter.UnsharpMask(radius=1.6, percent=68,
                                                 threshold=3))
    return out


def _fade(image: Image.Image, top: int, height: int, upwards=False):
    if height <= 0:
        return image
    top = max(0, min(top, image.height - 1))
    height = min(height, image.height - top)
    band = image.crop((0, top, image.width, top + height))
    px = band.load()
    for y in range(band.height):
        t = y / max(band.height - 1, 1)
        k = t ** 0.8 if upwards else (1.0 - t) ** 0.8
        for x in range(band.width):
            r, g, b = px[x, y]
            px[x, y] = (int(r * k), int(g * k), int(b * k))
    image.paste(band, (0, top))
    return image


def _fit_title(draw, title, max_w, start_size, tracking_ratio, font_path):
    size = start_size
    while size > 12:
        font = ImageFont.truetype(str(font_path), int(size))
        if _text_width(draw, title, font, size * tracking_ratio) <= max_w:
            return font, size * tracking_ratio
        size *= 0.97
    return ImageFont.truetype(str(font_path), 12), 0.0


def compose(art: Image.Image, w: int, h: int, cfg: Config, title_font,
            author_font) -> Image.Image:
    """Artwork at full width, with a band underneath carrying the lettering."""
    canvas = Image.new("RGB", (w, h), _hex(cfg.cover.band_background))
    draw = ImageDraw.Draw(canvas)

    gold = _hex(cfg.cover.title_color)
    white = _hex(cfg.cover.text_color)
    tagline = cfg.cover.tagline or ""

    pad = int(w * 0.042)
    tag_size = int(w * 0.026)
    author_size = int(w * 0.037)
    font_title, tracking = _fit_title(draw, cfg.title, w * 0.84, w * 0.125,
                                      0.115, title_font)
    title_h = int(font_title.size * 1.02)
    band_h = (pad + (int(tag_size * 2.1) if tagline else 0) + title_h
              + int(w * 0.028)
              + (int(author_size * 1.9) if cfg.author else 0) + pad)

    art = _fit_width(art, w)
    if cfg.cover.trim_bottom_ratio:
        art = art.crop((0, 0, w,
                        int(art.height * (1 - cfg.cover.trim_bottom_ratio))))
    art_h = min(art.height, h - band_h)
    if art.height > art_h:
        art = art.crop((0, 0, w, art_h))
    top = h - band_h - art.height
    canvas.paste(art, (0, top))
    _fade(canvas, top + art.height - int(h * 0.05), int(h * 0.05))
    if top > 0:
        _fade(canvas, top, int(h * 0.04), upwards=True)

    draw = ImageDraw.Draw(canvas)
    cx, y = w / 2.0, h - band_h + pad

    if tagline:
        f = ImageFont.truetype(str(author_font), tag_size)
        _draw_tracked(draw, (cx, y), tagline.upper(), f, gold, w * 0.008)
        y += int(tag_size * 2.1)

    _draw_tracked(draw, (cx, y), cfg.title, font_title, gold, tracking)
    y += title_h + int(w * 0.014)

    rule_w = int(w * 0.17)
    draw.line([(cx - rule_w / 2, y), (cx + rule_w / 2, y)], fill=gold,
              width=max(2, int(w * 0.002)))
    y += int(w * 0.022)

    if cfg.author:
        f = ImageFont.truetype(str(author_font), author_size)
        _draw_tracked(draw, (cx, y), cfg.author.upper(), f, white, w * 0.011)
    return canvas


def build_covers(cfg: Config) -> CoverReport:
    src = cfg.resolve(cfg.cover.image)
    if not src or not src.exists():
        raise CoverMissing(
            "No cover artwork. Ask the author for the image, then set "
            "`cover.image` in book.yaml. For a print edition it should be at "
            f"least {int(round((cfg.page.width_mm + 2 * cfg.page.bleed_mm) * MM))}"
            f"×{int(round((cfg.page.height_mm + 2 * cfg.page.bleed_mm) * MM))} px "
            "(300 dpi including bleed).")

    art = Image.open(src).convert("RGB")
    out = cfg.out_dir
    made: List[Path] = []
    warnings: List[str] = []

    w_px = int(round((cfg.page.width_mm + 2 * cfg.page.bleed_mm) * MM))
    h_px = int(round((cfg.page.height_mm + 2 * cfg.page.bleed_mm) * MM))
    effective_dpi = art.width / ((cfg.page.width_mm + 2 * cfg.page.bleed_mm)
                                 / 25.4)
    upscaled = art.width < w_px
    if upscaled:
        warnings.append(
            f"Artwork is {art.width}×{art.height} px, about "
            f"{effective_dpi:.0f} dpi at this trim size. It has been enlarged "
            f"to {w_px}×{h_px} with Lanczos and unsharp masking, which holds up "
            f"in print but does not create detail. Ask the author for a "
            f"{w_px}×{h_px} original if the print run matters.")

    title_font, author_font = _fonts(cfg)
    if title_font is None:
        raise RuntimeError("No usable TrueType font found for the cover")

    if cfg.cover.compose_title:
        print_img = compose(art, w_px, h_px, cfg, title_font, author_font)
    else:
        print_img = _fit_cover(art, w_px, h_px)

    base = cfg.output.basename
    jpg = out / f"{base}_cover_print_bleed.jpg"
    print_img.save(jpg, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
    made.append(jpg)

    pdf = out / f"{base}_cover_print.pdf"
    print_img.save(pdf, "PDF", resolution=DPI)
    made.append(pdf)

    # trimmed copy, used as page one of the complete PDF
    bleed_px = int(round(cfg.page.bleed_mm * MM))
    page_cover = print_img.crop((bleed_px, bleed_px,
                                 print_img.width - bleed_px,
                                 print_img.height - bleed_px))
    target = (int(round(cfg.page.width_mm * MM)),
              int(round(cfg.page.height_mm * MM)))
    if page_cover.size != target:
        page_cover = page_cover.resize(target, Image.LANCZOS)
    page_jpg = out / f"{base}_cover_page.jpg"
    page_cover.save(page_jpg, "JPEG", quality=94, dpi=(DPI, DPI), subsampling=0)
    made.append(page_jpg)

    kw = int(round(EBOOK_HEIGHT / EBOOK_RATIO))
    ebook = (compose(art, kw, EBOOK_HEIGHT, cfg, title_font, author_font)
             if cfg.cover.compose_title else _fit_cover(art, kw, EBOOK_HEIGHT))
    ebook_jpg = out / f"{base}_cover_ebook.jpg"
    ebook.save(ebook_jpg, "JPEG", quality=92, dpi=(72, 72))
    made.append(ebook_jpg)

    return CoverReport(files=made, source_px=(art.width, art.height),
                       effective_dpi=effective_dpi, upscaled=upscaled,
                       warnings=warnings)


def spine_width_mm(pages: int, paper: str = "cream-80") -> float:
    """Spine thickness for the wrap-around cover.

    Paper bulk varies by supplier; these are the common values, and the printer
    should always confirm before the cover is finalised.
    """
    caliper = {          # millimetres per sheet (two pages)
        "cream-80": 0.1250,
        "white-80": 0.1120,
        "white-90": 0.1300,
        "uncoated-100": 0.1450,
        "kdp-cream": 0.1275,
        "kdp-white": 0.1120,
    }.get(paper, 0.1250)
    return round(pages / 2 * caliper, 1)
