"""Markdown sources to typographic blocks.

Chapter files are written by a human (or by an agent) and usually carry traces
of how they were produced: word-count tallies, progress tables, editorial
to-dos. Those are stripped here, and the per-chapter reference lists are lifted
out so they can be merged into one bibliography at the end of the volume.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .config import ROMAN, Config
from .model import Block, Chapter, Part

# Lines that belong to the writing process, never to the book itself.
NOISE_PATTERNS = [
    r"^\s*\**\s*(capitolo|chapter)\s+\d+\s+(completato|complete|done)",
    r"^\s*\**\s*(totale|total)\s+(parte|part)\b",
    r"^\s*\**\s*(target|obiettivo)\s+(finale|final)\b",
    r"^\s*\**\s*(prossim[ao]|next)\s+(mista|tappa|parte|part|step|passo)\b",
    r"^\s*\**\s*(panoramica|overview)\s+(della\s+)?(parte|part)\b",
    r"(parole\s+stimate|word\s+count|estimated\s+words)",
    r"(citazioni\s+in-text\s+verificate|verified\s+citations)",
    r"~[\d.,]+\s+(parole|words)\b",
    r"^\s*\|?\s*(stato|status)\s*[:|]",
    r"\bTODO\b|\bFIXME\b|\[DA (SCRIVERE|COMPLETARE|VERIFICARE)\]",
    r"✅|❌|🚧",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

REFERENCE_HEADING = re.compile(
    r"^#{1,3}\s*.*\b(note\s+bibliografiche|bibliografia|riferimenti|"
    r"references|bibliography|works\s+cited|fonti)\b", re.IGNORECASE)

# `**Label:**` alone on a line acts as a fourth-level heading.
INLINE_HEADING = re.compile(r"^\*\*(.{2,90}?)[:.]?\*\*$")

SECTION_PREFIX = re.compile(r"^(?:Sezione|Section|Sez\.?)\s+[\d.]+\s*[:\-]\s*",
                            re.IGNORECASE)
LEADING_EMOJI = re.compile(r"^[\U0001F300-\U0001FAFF☀-➿️]+\s*")


# ------------------------------------------------------------- microtype ---

def clean_inline(text: str, language: str = "it") -> str:
    """Normalise spacing and punctuation, keeping the inline markup intact."""
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=\w)\s+-\s+(?=\w)", " — ", text)
    text = text.replace("...", "…")
    if language.startswith("it") or language.startswith("fr"):
        text = re.sub(r'"([^"]{1,400})"', r"«\1»", text)
    else:
        text = re.sub(r'"([^"]{1,400})"', r"“\1”", text)
    text = text.replace("'", "’")
    return text


def is_noise(line: str) -> bool:
    return bool(NOISE_RE.search(line))


# ---------------------------------------------------------------- blocks ---

def parse_blocks(lines: Iterable[str], cfg: Config) -> List[Block]:
    lines = list(lines)
    lang = cfg.language
    strip_noise = cfg.source.strip_production_noise
    blocks: List[Block] = []
    buf: List[str] = []

    def flush():
        if not buf:
            return
        text = clean_inline(" ".join(buf), lang)
        buf.clear()
        if not text:
            return
        m = INLINE_HEADING.match(text)
        blocks.append(Block("h4", m.group(1).strip()) if m
                      else Block("para", text))

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line:
            flush()
            i += 1
            continue

        if strip_noise and is_noise(line):
            flush()
            i += 1
            continue

        if line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                raw = lines[i].strip()
                if not re.match(r"^\s*\|[\s:|-]+\|\s*$", raw):
                    cells = [clean_inline(c, lang)
                             for c in raw.strip("|").split("|")]
                    if not (strip_noise and any(is_noise(c) for c in cells)):
                        rows.append(cells)
                i += 1
            if rows:
                blocks.append(Block("table", rows=rows))
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line):
            flush()
            if blocks and blocks[-1].kind != "rule":
                blocks.append(Block("rule"))
            i += 1
            continue

        if line.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip("> ").strip())
                i += 1
            blocks.append(Block("quote", clean_inline(" ".join(quote), lang)))
            continue

        m = re.match(r"^(#{2,6})\s+(.*)$", line)
        if m:
            flush()
            title = clean_inline(m.group(2), lang)
            title = LEADING_EMOJI.sub("", title)
            title = SECTION_PREFIX.sub("", title)
            if strip_noise and is_noise(title):
                i += 1
                continue
            level = len(m.group(1))
            blocks.append(Block("h2" if level == 2 else
                                "h3" if level == 3 else "h4", title))
            i += 1
            continue

        m = re.match(r"^[-*+]\s+(.*)$", line)
        if m:
            flush()
            blocks.append(Block("bullet", clean_inline(m.group(1), lang)))
            i += 1
            continue

        m = re.match(r"^(\d+)[.)]\s+(.*)$", line)
        if m:
            flush()
            blocks.append(Block("number", clean_inline(m.group(2), lang),
                                level=int(m.group(1))))
            i += 1
            continue

        buf.append(line)
        i += 1

    flush()

    while blocks and blocks[0].kind == "rule":
        blocks.pop(0)
    while blocks and blocks[-1].kind in ("rule", "gap", "h2", "h3", "h4"):
        blocks.pop()
    return blocks


# ------------------------------------------------------------ references ---

def split_references(lines: List[str], cfg: Config) -> Tuple[List[str], List[str]]:
    if not cfg.source.extract_chapter_references:
        return lines, []
    for idx, line in enumerate(lines):
        if REFERENCE_HEADING.match(line.strip()):
            body = lines[:idx]
            while body and body[-1].strip() in ("", "---"):
                body.pop()
            return body, collect_reference_entries(lines[idx + 1:])
    return lines, []


def collect_reference_entries(lines: List[str]) -> List[str]:
    entries, buf = [], []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("---"):
            if buf:
                entries.append(" ".join(buf))
                buf = []
            continue
        if is_noise(s):
            continue
        if s.startswith("*") and s.endswith("*") and len(s) < 200:
            continue
        s = re.sub(r"^[-*+]\s+", "", s)
        buf.append(s)
    if buf:
        entries.append(" ".join(buf))
    return [re.sub(r"\s+", " ", e).strip() for e in entries if len(e) > 25]


_AUTHOR_YEAR = re.compile(r"^([^(]{2,140}?)\s*\((\d{4}[a-z]?|s\.d\.|n\.d\.)")


def reference_sort_key(entry: str) -> Tuple[str, str]:
    m = _AUTHOR_YEAR.match(entry)
    if m:
        return (m.group(1).lower().lstrip("*_ "), m.group(2))
    return (re.sub(r"^[*_]+", "", entry).lower()[:60], "")


def dedupe_references(entries: List[str]) -> List[str]:
    """Merge the per-chapter lists, keeping the richest version of each entry."""
    seen = {}
    for e in entries:
        key = re.sub(r"[^a-z0-9]+", "", e.lower())[:70]
        if key in seen and len(e) <= len(seen[key]):
            continue
        seen[key] = e
    return sorted(seen.values(), key=reference_sort_key)


# ------------------------------------------------------------- chapters ---

def parse_chapter_file(path: Path, cfg: Config) -> Tuple[Chapter, Optional[Part]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    chap_re = re.compile(cfg.source.chapter_regex)
    part_re = re.compile(cfg.source.part_regex)

    chap_idx = part_idx = None
    chap_match = None
    for idx, line in enumerate(lines):
        s = line.strip()
        if chap_idx is None:
            m = chap_re.match(s)
            if m:
                chap_idx, chap_match = idx, m
        if part_idx is None and part_re.match(s):
            part_idx = idx

    if chap_idx is None:
        # A file with a single H1 that is not numbered is still a chapter.
        for idx, line in enumerate(lines):
            m = re.match(r"^#\s+(.*)$", line.strip())
            if m and not part_re.match(line.strip()):
                chap_idx = idx
                chap_match = None
                break
    if chap_idx is None:
        raise ValueError(f"No chapter heading found in {path}")

    if chap_match:
        number = int(chap_match.group(1))
        full = clean_inline(chap_match.group(2), cfg.language)
    else:
        number = _number_from_name(path)
        full = clean_inline(re.sub(r"^#\s+", "", lines[chap_idx].strip()),
                            cfg.language)

    sep = cfg.source.title_split
    if sep and sep in full:
        title, subtitle = [p.strip() for p in full.split(sep, 1)]
    elif " - " in full:
        title, subtitle = [p.strip() for p in full.split(" - ", 1)]
    else:
        title, subtitle = full, ""

    # A part opener may sit at the top of the first chapter file of that part.
    part = None
    if part_idx is not None and part_idx < chap_idx:
        heading = lines[part_idx].strip()
        if not re.search(r"riepilogo|recap|transizione|transition|summary",
                         heading, re.IGNORECASE):
            m = part_re.match(heading)
            roman = m.group(1) if m else ""
            ptitle = (m.group(2).strip() if m and m.lastindex and m.lastindex > 1
                      else "")
            intro = parse_blocks(lines[part_idx + 1:chap_idx], cfg)
            part = Part(number=_roman_to_int(roman), roman=roman,
                        title=clean_inline(ptitle, cfg.language) or roman,
                        intro=intro)

    body, refs = split_references(lines[chap_idx + 1:], cfg)
    chapter = Chapter(number=number, title=title, subtitle=subtitle,
                      blocks=parse_blocks(body, cfg), references=refs,
                      source=str(path))
    return chapter, part


def _number_from_name(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    return int(m.group(1)) if m else 0


def _roman_to_int(roman: str) -> int:
    roman = (roman or "").upper()
    if roman in ROMAN:
        return ROMAN.index(roman)
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = prev = 0
    for ch in reversed(roman):
        v = values.get(ch, 0)
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total or 1


def chapter_files(cfg: Config) -> List[Path]:
    root = (cfg.root / cfg.source.root).resolve()
    files = [p for p in root.glob(cfg.source.pattern) if p.is_file()]
    excluded = set()
    for pattern in cfg.source.exclude:
        excluded.update(p.resolve() for p in root.glob(pattern))
    files = [p for p in files if p.resolve() not in excluded]
    return sorted(files, key=lambda p: (_sort_key(p)))


def _sort_key(path: Path):
    """Sort by the numbers in the path: 02_part/chapter_10.md before _11."""
    nums = [int(n) for n in re.findall(r"\d+", str(path))]
    return (nums, str(path).lower())


def load_parts(cfg: Config) -> List[Part]:
    """Group chapters into parts, from the config or from the sources.

    A part opener found at the top of a chapter file marks that chapter as the
    first of the part, which is what decides where each part begins.
    """
    chapters = []
    openings = []          # (first chapter number, Part)
    for f in chapter_files(cfg):
        chapter, part = parse_chapter_file(f, cfg)
        chapters.append(chapter)
        if part:
            openings.append((chapter.number, part))

    chapters.sort(key=lambda c: c.number)
    openings.sort(key=lambda o: o[0])
    discovered = {num: part for num, part in openings}
    if not chapters:
        raise ValueError(
            f"No chapters matched {cfg.source.pattern!r} under "
            f"{(cfg.root / cfg.source.root)}")

    if cfg.parts:
        parts = []
        assigned = set()
        for spec in cfg.parts:
            p = Part(number=spec.number, roman=spec.roman, title=spec.title)
            wanted = spec.chapters
            for c in chapters:
                if (wanted and c.number in wanted) or \
                   (not wanted and c.number not in assigned):
                    p.chapters.append(c)
                    assigned.add(c.number)
            found = discovered.get(spec.number)
            if spec.intro_file:
                path = cfg.resolve(spec.intro_file)
                if path and path.exists():
                    p.intro = parse_blocks(
                        path.read_text(encoding="utf-8").splitlines(), cfg)
            if not p.intro and found:
                p.intro = found.intro
            parts.append(p)
        leftovers = [c for c in chapters if c.number not in assigned]
        if leftovers and parts:
            parts[-1].chapters.extend(leftovers)
        return [p for p in parts if p.chapters]

    if openings:
        # renumber the parts in reading order, whatever the source said
        for i, (_, part) in enumerate(openings, 1):
            part.number = i
            if not part.roman:
                part.roman = ROMAN[i] if i < len(ROMAN) else str(i)

        orphans = [c.number for c in chapters if c.number < openings[0][0]]
        if orphans:
            import sys
            print(f"  NOTE    chapters {orphans} come before the first part "
                  f"heading found in the sources and have been folded into "
                  f"\"{openings[0][1].title}\". Declare the missing part under "
                  f"`parts:` in book.yaml if they belong to one of their own.",
                  file=sys.stderr)
        for c in chapters:
            target = openings[0][1]
            for first, part in openings:
                if first <= c.number:
                    target = part
            target.chapters.append(c)
        return [p for _, p in openings if p.chapters]

    # No parts at all: a flat book.
    single = Part(number=1, roman="", title="")
    single.chapters = chapters
    return [single]
