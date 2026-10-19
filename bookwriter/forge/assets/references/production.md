# Production: cover, print, export, pre-flight

## The cover comes from the author

This is the one input the pipeline cannot manufacture. Establish in Phase 1
and confirm in Phase 5 which of three cases applies:

1. **Artwork exists.** Get the file. Check resolution and aspect ratio
   immediately, not at the end.
2. **Artwork is to be made.** That is a commission, whether it goes to a
   designer or to an image model. Agree it explicitly before producing
   anything, and present what you generate as a proposal.
3. **Typographic cover.** Type on a plain or textured ground — often the best
   outcome for an essay, and the only one fully under your control.

### Resolution

At 300 dpi the artwork must be at least, in pixels:

```
width  = (trim_width_mm  + 2 × bleed_mm) × 11.811
height = (trim_height_mm + 2 × bleed_mm) × 11.811
```

| Trim | With 3 mm bleed | Minimum pixels |
|---|---|---|
| A5 148×210 | 154×216 | 1819 × 2551 |
| B-format 129×198 | 135×204 | 1594 × 2409 |
| 5.5×8.5 in 140×216 | 146×222 | 1724 × 2622 |
| 6×9 in 152×229 | 158×235 | 1866 × 2775 |

Smaller artwork can be enlarged — the pipeline does it with Lanczos plus
unsharp masking and reports the effective dpi — but enlargement does not create
detail. Give the author the number and let them decide.

### Aspect ratio

Cover artwork rarely matches the trim. The pipeline scales to full width and
adds a band underneath for the lettering rather than cropping the sides,
because artwork usually carries text or figures near the edges that a
horizontal crop would mutilate. `cover.trim_bottom_ratio` removes a strip from
the foot when the artwork has its own lettering there.

For the ebook the store wants 1.6:1 (1600×2560). The same composition runs at
that ratio.

### Spine

Not generated: it depends on the printer's paper. Approximate thickness:

```
spine_mm = pages / 2 × caliper_mm
```

with caliper per sheet around 0.125 mm for 80 g/m² cream, 0.112 for 80 g/m²
white, 0.130 for 90 g/m² white. 200 pages of cream 80 g ≈ 12.5 mm. Always
confirm with the printer before the wrap-around cover is finalised, and add
their required tolerance (KDP allows ±1.6 mm of drift on the spine).

`bookgen.cover.spine_width_mm(pages, paper)` gives the estimate.

---

## Page setup

**Trim** is the finished page. **Bleed** is the overhang, 3 mm standard, and
applies to the cover, never to the interior of a text-only book.

**Margins are asymmetric.** The inner margin faces the binding and part of it
disappears into the fold, so it must be wider than the outer one. The pipeline
defaults to ratios of the trim (inner 12.2%, outer 10.1%, top 7.6%, bottom
8.6%); at A5 that is 18 / 15 / 16 / 18 mm. For books over 400 pages add 2–3 mm
to the inner margin: a thick spine swallows more.

**Safety.** No text closer than 6.4 mm to any trimmed edge — KDP rejects below
that, and cutting tolerance is ±1.5 mm anyway.

**Measure.** Around 66 characters per line is comfortable. The pipeline derives
the body size from the measure; override only with reason.

## The conventions that look arbitrary and are not

- **Chapters open on a recto** (odd, right-hand page). The verso before it is
  left blank. Anything else reads as an error to anyone who handles books.
- **The page count is padded to an even number.** Sheets have two sides;
  perfect binding cannot produce an odd count.
- **Front matter is numbered in roman**, the body restarts at arabic 1. The
  contents page must show the folio as printed, not the sheet index.
- **Opening pages carry no running head**, only the folio, centred at the foot.
- **Running heads**: book title on the verso, chapter title on the recto,
  folio outside.
- **When a cover is bound into the PDF, it is followed by a blank verso.** With
  one extra page every recto becomes a verso and the mirrored margins fall on
  the wrong side of the fold.

## Print channels

**Amazon KDP.** Interior and cover as *separate* PDFs. Interior at exact trim,
no bleed unless images run off the page, no crop marks, all fonts embedded.
Cover as one wrap-around PDF (back + spine + front) at trim plus 3.2 mm bleed,
built to KDP's spine calculator. Minimum 24 pages. EPUB for the Kindle edition;
KDP has accepted EPUB natively since 2022 and MOBI is dead.

**IngramSpark.** Similar, stricter: PDF/X-1a:2001, CMYK for anything coloured,
fonts embedded, and their own template for the cover.

**A local printer.** Ask three questions before exporting: single file or
separate cover; crop marks and bleed or trim only; and which colour profile.
Then give them exactly that.

**Direct PDF.** The complete file with the cover as page one is the right
deliverable — that is what `pdf_with_cover: true` produces.

## Export

```bash
python -m bookgen build <project>              # everything
python -m bookgen build <project> --only pdf   # one format
python -m bookgen check <project>              # re-run pre-flight
python -m bookgen info  <project>              # what will be composed
```

Files produced in `output/`:

| File | For |
|---|---|
| `<base>_<W>x<H>.pdf` | reading, direct distribution — cover bound in |
| `<base>_interior.pdf` | the printer, KDP Print |
| `<base>_cover_print_bleed.jpg` / `.pdf` | the printed cover |
| `<base>_cover_page.jpg` | the trimmed cover used as page one |
| `<base>_cover_ebook.jpg` | store thumbnail, 1600×2560 |
| `<base>.docx` | the editable original |
| `<base>_ebook.epub` | KDP, Kobo, Apple Books |

### DOCX notes

The contents page is a `TOC` field: in Word, Ctrl+A then F9 fills it. The recto
running head is a `STYLEREF` field bound to the chapter-title style, so it
follows the text after any edit. Styles are named in the book's language;
changing a style changes the whole volume.

### EPUB notes

Reflowable, no embedded fonts, sizes in em. Cover declared both as an OPF
property and as an XHTML page — Amazon's converter looks for both. `nav.xhtml`
for EPUB 3 and `toc.ncx` for older Kindles.

## Pre-flight

Run automatically after every build. What it checks:

- page count parity, and page size against the requested trim
- mixed page sizes in one file
- fonts declared but not embedded
- text closer than 6.4 mm to the trim
- chapter openings landing on a verso
- EPUB: mimetype stored first and uncompressed, container and OPF resolvable,
  every manifest entry present, spine ids resolvable, a nav document, a cover
  image, required metadata, no broken internal links
- DOCX: TOC field, STYLEREF, mirrored margins

It does not replace **Kindle Previewer** for the ebook or a **printed proof**
for the paper edition. Say so when handing over. `epubcheck` is worth running
if Java is available; the built-in checks cover the same failure modes but not
the full specification.

## Fonts and licensing

The pipeline picks the first available family from a preference list and
reports what it used. Microsoft system fonts (Constantia, Corbel, Georgia,
Cambria) are excellent for typesetting and their licence covers use on a
licensed Windows machine — it does not clearly cover a commercial print run
produced by a third party. For anything sold, substitute open fonts: **EB
Garamond** or **Libre Baskerville** for the text, **Lato** or **Source Sans**
for the display. Set `typography.serif` and `typography.sans` in `book.yaml`;
nothing else changes. Raise this in the handover every time.
