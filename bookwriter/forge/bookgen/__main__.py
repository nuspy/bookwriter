"""Command line entry point.

    python -m bookgen init   [DIR]             scaffold a new book project
    python -m bookgen status [DIR]             what is on disk vs planned
    python -m bookgen build  [DIR] [--only pdf,docx,epub,cover]
    python -m bookgen check  [DIR]
    python -m bookgen info   [DIR]

Any agent can drive the pipeline through this interface; nothing here depends
on a particular assistant or IDE.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from . import config as config_mod
from .assemble import build_book, stats

def _template_dir() -> Path:
    """Locate templates/ by walking up from this file, so the package works
    whether it sits inside the skill, is symlinked, or has been copied."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        for candidate in (parent / "templates",
                          parent / "assets" / "templates",
                          parent / "skills" / "book-forge" / "templates"):
            if (candidate / "book.yaml").exists():
                return candidate
    return here.parents[2] / "templates"


TEMPLATE_DIR = _template_dir()


def _print(msg=""):
    try:
        print(msg)
    except UnicodeEncodeError:                                # narrow console
        print(msg.encode("ascii", "replace").decode())


# ------------------------------------------------------------------- init ---

def cmd_init(args) -> int:
    target = Path(args.directory).resolve()
    target.mkdir(parents=True, exist_ok=True)

    existing = config_mod.find_config(target)
    if existing and not args.force:
        _print(f"{existing.name} already exists in {target}. Use --force to "
               f"overwrite.")
        return 1

    template = TEMPLATE_DIR / "book.yaml"
    if not template.exists():
        _print(f"Template not found at {template}")
        return 1
    shutil.copyfile(template, target / "book.yaml")

    for sub in ("chapters", "front", "back", "research", "output"):
        (target / sub).mkdir(exist_ok=True)
    sample = TEMPLATE_DIR / "chapter.md"
    if sample.exists() and not any((target / "chapters").glob("*.md")):
        shutil.copyfile(sample, target / "chapters" / "01_chapter.md")
    spec = TEMPLATE_DIR / "MASTER_SPEC.md"
    if spec.exists() and not (target / "MASTER_SPEC.md").exists():
        shutil.copyfile(spec, target / "MASTER_SPEC.md")

    _print(f"Project scaffolded in {target}")
    _print("  book.yaml        edit the metadata, trim size and cover path")
    _print("  MASTER_SPEC.md   the brief the writing agent works from")
    _print("  chapters/        one markdown file per chapter")
    _print("  front/ back/     introduction, notice, glossary, author note")
    _print("  research/        sources and notes gathered before writing")
    return 0


# ------------------------------------------------------------------ build ---

def cmd_build(args) -> int:
    cfg = config_mod.load(Path(args.directory))
    only = [f.strip().lower() for f in args.only.split(",")] if args.only \
        else None
    formats = only or (list(cfg.output.formats) + ["cover"])

    book = build_book(cfg)
    st = stats(book)
    _print(f"{cfg.title} — {st['chapters']} chapters, {st['parts']} part(s), "
           f"{st['words']:,} words, {st['references']} references"
           .replace(",", "."))

    out = cfg.out_dir
    base = cfg.output.basename
    produced = {}
    cover_page = None

    if "cover" in formats or cfg.output.pdf_with_cover:
        from .cover import CoverMissing, build_covers
        try:
            t0 = time.time()
            rep = build_covers(cfg)
            cover_page = next((p for p in rep.files
                               if p.name.endswith("_cover_page.jpg")), None)
            for f in rep.files:
                _print(f"  cover   {f.name}  "
                       f"({f.stat().st_size / 1024:.0f} KB)")
            for w in rep.warnings:
                _print(f"  NOTE    {w}")
            _print(f"  covers built in {time.time() - t0:.1f}s")
        except CoverMissing as e:
            _print(f"  COVER MISSING — {e}")
            if not args.allow_missing_cover:
                return 2

    if "pdf" in formats:
        from .pdf import build_pdf
        t0 = time.time()
        path = out / f"{base}_{int(cfg.page.width_mm)}x{int(cfg.page.height_mm)}.pdf"
        interior = (out / f"{base}_interior.pdf"
                    if cfg.output.pdf_interior_copy and cover_page else None)
        info = build_pdf(book, path, cfg,
                         cover=cover_page if cfg.output.pdf_with_cover else None,
                         interior_copy=interior)
        produced["pdf"] = path
        _print(f"  PDF     {path.name}  {info['pages']} pages  "
               f"({path.stat().st_size / 1024:.0f} KB)  "
               f"{time.time() - t0:.1f}s")
        for line in info["fonts"]:
            _print(f"          font {line}")
        if interior:
            produced["pdf_interior"] = interior
            _print(f"  PDF     {interior.name}  interior only, for the printer")

    if "docx" in formats:
        from .docx_out import build_docx
        from .typography import register_fonts, theme_from_config
        theme = register_fonts(theme_from_config(cfg), cfg)
        t0 = time.time()
        path = out / f"{base}.docx"
        build_docx(book, path, cfg, serif=theme.serif_name,
                   sans=theme.sans_name)
        produced["docx"] = path
        _print(f"  DOCX    {path.name}  "
               f"({path.stat().st_size / 1024:.0f} KB)  {time.time() - t0:.1f}s")

    if "epub" in formats:
        from .epub import build_epub
        t0 = time.time()
        path = out / f"{base}_ebook.epub"
        ebook_cover = out / f"{base}_cover_ebook.jpg"
        build_epub(book, path, cfg,
                   cover=ebook_cover if ebook_cover.exists() else None)
        produced["epub"] = path
        _print(f"  EPUB    {path.name}  "
               f"({path.stat().st_size / 1024:.0f} KB)  {time.time() - t0:.1f}s")

    if args.check and produced:
        from .check import check_all
        _print("\nPre-flight:")
        report = check_all(cfg, {k: v for k, v in produced.items()})
        _print(report.render())
        if not report.ok():
            return 3
    return 0


# ------------------------------------------------------------------ check ---

def cmd_check(args) -> int:
    cfg = config_mod.load(Path(args.directory))
    out = cfg.out_dir
    targets = {}
    for p in sorted(out.glob("*")):
        if p.suffix.lower() in (".pdf", ".epub", ".docx") \
                and "cover" not in p.stem:
            targets[p.stem] = p
    if not targets:
        _print(f"Nothing to check in {out}")
        return 1
    from .check import check_all
    report = check_all(cfg, targets)
    _print(report.render())
    return 0 if report.ok() else 3


# ----------------------------------------------------------------- status ---

def _planned_chapters(root: Path):
    """Chapter numbers and titles planned in MASTER_SPEC.md.

    The spec lists chapters either in a markdown table (`| 7 | Title | ... |`)
    or in running text (`7. Title ·`). Both are read, because an agent writing
    the spec will use whichever fits the book.
    """
    spec = root / "MASTER_SPEC.md"
    if not spec.exists():
        return {}
    import re
    planned = {}
    for line in spec.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*\|\s*(\d{1,3})\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            planned.setdefault(int(m.group(1)), m.group(2).strip())
            continue
        for m in re.finditer(r"(?:^|·\s*)(\d{1,3})\.\s+([^·\n]{3,70})", line):
            planned.setdefault(int(m.group(1)), m.group(2).strip(" *_"))
    return planned


def cmd_status(args) -> int:
    """What is actually on disk, against what the spec plans.

    This exists because an agent's memory of having written a chapter is not
    evidence that the file exists. Only the file system is.
    """
    root = Path(args.directory).resolve()
    try:
        cfg = config_mod.load(root)
    except FileNotFoundError as e:
        _print(str(e))
        return 1

    from .parse import chapter_files, parse_chapter_file

    found, unreadable = {}, []
    for f in chapter_files(cfg):
        try:
            ch, _ = parse_chapter_file(f, cfg)
            found[ch.number] = (f, len(f.read_text(encoding="utf-8").split()),
                                ch.title)
        except Exception as exc:
            unreadable.append((f, exc))

    planned = _planned_chapters(root)
    total = max(len(planned), max(found, default=0))

    _print(f"project    {root}")
    _print(f"title      {cfg.title}")
    _print(f"on disk    {len(found)} chapter file(s)"
           + (f" of {len(planned)} planned" if planned else ""))

    words = sum(w for _, w, _ in found.values())
    _print(f"words      {words:,}".replace(",", "."))

    if found:
        thin = [n for n, (_, w, _) in sorted(found.items()) if w < 400]
        _print(f"range      chapters {min(found)}–{max(found)}")
        if thin:
            _print(f"thin       {thin} — under 400 words, check they are complete")

    missing = [n for n in range(1, total + 1) if n not in found]
    if missing:
        _print("")
        _print(f"MISSING    {len(missing)} chapter(s) not on disk: {missing}")
        for n in missing[:10]:
            _print(f"           {n}. {planned.get(n, '(not in the spec either)')}")
        if len(missing) > 10:
            _print(f"           … and {len(missing) - 10} more")
        _print("")
        _print("Anything you believe you wrote but that is listed above was")
        _print("never saved. Write it to chapters/ now, from the draft if you")
        _print("still have it, or rewrite it.")

    for f, exc in unreadable:
        _print(f"UNREADABLE {f.name}: {exc}")

    for label, folder in (("front", root / "front"), ("back", root / "back")):
        files = sorted(folder.glob("*.md")) if folder.exists() else []
        _print(f"{label:<10} {', '.join(f.name for f in files) or '(none)'}")

    cover = cfg.resolve(cfg.cover.image)
    _print(f"cover      {cover.name if cover and cover.exists() else 'MISSING — ask the author'}")

    out = root / cfg.output.directory
    built = sorted(p.name for p in out.glob("*")
                   if p.suffix.lower() in (".pdf", ".docx", ".epub")) \
        if out.exists() else []
    _print(f"built      {', '.join(built) or '(nothing exported yet)'}")

    return 3 if (missing or unreadable) else 0


# ------------------------------------------------------------------- info ---

def cmd_info(args) -> int:
    cfg = config_mod.load(Path(args.directory))
    book = build_book(cfg)
    st = stats(book)
    _print(f"title      {cfg.title}")
    _print(f"author     {cfg.author or '(not set)'}")
    _print(f"language   {cfg.language}")
    _print(f"trim       {cfg.page.width_mm} × {cfg.page.height_mm} mm")
    _print(f"margins    inner {cfg.page.margin_inner_mm} · outer "
           f"{cfg.page.margin_outer_mm} · top {cfg.page.margin_top_mm} · "
           f"bottom {cfg.page.margin_bottom_mm} mm")
    _print(f"type       {cfg.typography.body_size}/{cfg.typography.leading} pt"
           f", hyphenation {cfg.typography.hyphenation or 'off'}")
    _print(f"content    {st['chapters']} chapters in {st['parts']} part(s), "
           f"{st['words']} words")
    _print(f"front      {', '.join(st['front_sections']) or '(none)'}")
    _print(f"back       {', '.join(st['back_sections']) or '(none)'}")
    _print(f"references {st['references']}")
    cover = cfg.resolve(cfg.cover.image)
    _print(f"cover      {cover if cover and cover.exists() else 'MISSING — ask the author'}")
    for p in book.parts:
        _print(f"  {('Part ' + p.roman) if p.roman else 'Body'}: "
               f"{p.title} — chapters "
               f"{[c.number for c in p.chapters]}")
    return 0


# ------------------------------------------------------------------- main ---

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bookgen",
                                 description="Compose and export a book")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold a new project")
    p.add_argument("directory", nargs="?", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build", help="compose and export")
    p.add_argument("directory", nargs="?", default=".")
    p.add_argument("--only", help="comma separated: pdf,docx,epub,cover")
    p.add_argument("--check", action="store_true", default=True)
    p.add_argument("--no-check", dest="check", action="store_false")
    p.add_argument("--allow-missing-cover", action="store_true",
                   help="build the interior even without artwork")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("check", help="run pre-flight on the built files")
    p.add_argument("directory", nargs="?", default=".")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("status", help="what is on disk vs what the spec plans")
    p.add_argument("directory", nargs="?", default=".")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("info", help="summarise the project")
    p.add_argument("directory", nargs="?", default=".")
    p.set_defaults(func=cmd_info)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        _print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
