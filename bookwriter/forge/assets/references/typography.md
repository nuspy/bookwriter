# Typography

The defaults derive from the trim size and are usually right. Change them for a
reason you can state.

## What is derived

- **Body size** from the measure: `min(12, max(9.2, measure_mm × 0.092))` pt,
  which lands near 66 characters a line. A5 with a 115 mm measure gives
  10.6 pt.
- **Leading** at 1.38 times the body size. Tighter than 1.3 tires the eye over
  long stretches; looser than 1.5 breaks the block apart.
- **Hyphenation** from the book's language, through pyphen. Justified text
  without hyphenation opens rivers of white space — this is not optional in
  Italian, German or Spanish.

Override any of it in `book.yaml` under `typography:`.

## Type choices

Text in a serif with real italics and a bold that holds at 10 pt. The
preference order is EB Garamond, Constantia, Georgia, Palatino, Times. Display
and running heads in a humanist sans for contrast: Lato, Corbel, Candara,
Calibri, Arial.

Two families are enough. A third reads as indecision.

## The scale

Everything is a ratio of the body size, so the proportions survive a change of
trim:

| Element | multiple of body size |
|---|---|
| Chapter title | 1.84 |
| Part title | 1.98 |
| Section (h2) | 1.19 |
| Subsection (h3) | 1.05, italic |
| Fourth level (h4) | 0.86, sans |
| Table cell | 0.81 |
| Bibliography entry | 0.87 |
| Running head | 0.72 |

## Paragraphs

A first-line indent of 5 mm marks continuation. Suppress it after a heading, a
list, a table or a quotation, where the paragraph starts a new thought rather
than continuing one. Never combine an indent with space between paragraphs:
one signal is enough, two is noise.

## Details that separate a book from a document

- Widows and orphans off — a single line stranded on a page is visible even to
  readers who could not name the problem
- Headings kept with the paragraph that follows
- Tables that do not begin in the last 34 mm of a page
- Table headers repeated when a table breaks across pages
- Quotation marks matching the language: caporali in Italian and French,
  curly doubles in English, low-high in German
- Em dashes spaced in Italian and French, unspaced in English
- Ellipsis as a single character, not three periods
- Ornaments drawn as vectors rather than set from a glyph the font may lack

## Ebook typography

A different medium with different rules. No fixed sizes, no embedded fonts
unless the design genuinely requires them, everything in em. The reader chooses
the typeface and the size; the stylesheet controls structure and proportion,
not appearance. Keep justification on and hyphenation off — e-readers hyphenate
badly.
