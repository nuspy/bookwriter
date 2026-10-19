# Troubleshooting

## The command will not start

**`No module named bookgen.__main__; 'bookgen' is a package and cannot be
directly executed`** — Python found a *different* `bookgen` before the real
one. Python puts the current directory at the head of the search path, so this
happens whenever you run the command from a directory that contains its own
`bookgen/` folder — for instance an older project that kept a copy of the
composer.

Run from somewhere else, or pass the project as an argument instead of `cd`-ing
into a directory that shadows the package:

```bash
cd /any/neutral/directory
PYTHONPATH=<skill>/scripts python -m bookgen status /absolute/path/to/project
```

**Nothing at all is printed** — check `PYTHONPATH` is in the form the
interpreter expects. On Windows, native Python wants `C:/Users/...`, not the
Git-Bash style `/c/Users/...`; the latter silently resolves to nothing.

## The build stops

**`No book.yaml found`** — run `python -m bookgen init <dir>` first, or point
the command at the directory that holds it.

**`No chapters matched ...`** — `source.pattern` does not match the files.
Inspect with `python -m bookgen info <dir>`. Chapters kept in numbered folders
need `"*/capitolo_*.md"`; a flat directory needs `"chapters/*.md"`.

**`No chapter heading found in ...`** — the file's H1 does not match
`source.chapter_regex`. Either fix the heading or widen the pattern. A file
with a plain unnumbered H1 is still accepted and numbered from its filename.

**`CoverMissing`** — working as intended. Ask the author for the artwork. To
compose the interior in the meantime, pass `--allow-missing-cover`.

**No serif family found** — nothing from the preference list is installed.
Install one, or name an installed family in `typography.serif`.

## The output is wrong

**Chapters open on a left-hand page.** Something added an odd number of pages
before the body. If a cover was bound in by hand, it needs its blank verso too.

**The contents page shows the wrong numbers.** The contents prints the folio as
composed. If it disagrees with the printed page, the arabic numbering does not
start where the body starts — check that no front-matter section slipped after
the marker.

**Word says "No table of contents entries found."** The TOC field reads outline
levels. Chapter and part styles carry them; apparatus titles are deliberately
excluded so that "Contents" does not list itself.

**Running heads show the wrong chapter in Word.** The STYLEREF field follows
the chapter-title style. A heading retyped in a different style disappears from
both the running head and the contents.

**Text sits too close to the trim.** Increase the margins in `book.yaml`. Below
6.4 mm KDP rejects the file outright.

**A table runs past the measure.** Column widths are proportional to content.
Shorten the header labels, move the table to an appendix, or turn it into a
list — six columns do not fit a 115 mm measure at any size worth reading.

## The store rejects the file

**KDP rejects the interior.** Usually a font that is not embedded, a page size
that differs from the declared trim, or mixed page sizes in one file.
`python -m bookgen check` reports all three.

**KDP rejects the cover.** Wrong spine width for the final page count, or
missing bleed. Recalculate from the actual page count.

**The EPUB fails validation.** Run `python -m bookgen check`. If Java is
available, `epubcheck` is stricter and worth a pass before upload. The frequent
causes are a compressed mimetype, a manifest entry whose file is absent, and a
broken link in the navigation document.

**The Kindle preview looks wrong.** Check it in Kindle Previewer 3, not in a
browser. Fixed sizes in the stylesheet and images wider than the screen are the
usual culprits.

## Performance

A 90,000-word book composes in a few seconds. If it takes minutes something is
looping — most often a `source.pattern` that also matches the output directory,
feeding generated files back in as sources.
