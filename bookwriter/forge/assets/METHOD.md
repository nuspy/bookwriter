---
name: book-forge
description: Write and produce a complete book — interview the author, research the subject, draft the chapters, typeset it, and export a print-ready PDF, an editable DOCX and a Kindle-ready EPUB. Use when someone wants to create a book, a manual, a novel or an essay, when they have chapters that need to become a finished volume, or when they ask for print or KDP preparation.
license: MIT
---

# Book Forge

You are producing a book, not a long document. A book is an object with a
trim size, a gutter, a folio sequence and a cover — decide those things with
the author at the start, because they constrain everything downstream.

Work in the language the author is using with you. Ask which language the
**book** is written in if it is not already established; it may differ from the
language of your conversation, and it drives hyphenation, section labels and
quotation marks.

> ## The rule that comes before everything
>
> **Every chapter goes to `<project>/chapters/NN_name.md` as soon as it is
> written, before the next one begins.** Not into your reply, not into a
> scratch buffer, not into a cache under a temp directory: into the project
> directory the user named, with a file-writing tool.
>
> A book spans many sessions. Your context does not. Whatever is not on disk
> when a session ends is gone, and no one can recover it — not you in the next
> session, not another agent, not the user.
>
> The same applies to `MASTER_SPEC.md`, to the research files, and to front and
> back matter. Write first, report second.
>
> `python -m bookgen status <project>` tells you what actually exists. It is the
> only progress report worth trusting.

## Any agent can run this

The method below is agent-independent and the composer is a plain Python
package with a command-line interface. Hermes, Claude Code, Codex, a shell
script or a person can drive it identically. See `references/agents.md` for
what each runtime needs to know — in particular where its file-writing tool
actually writes, which is the thing that most often goes wrong.

## The seven phases

1. **Interview** — establish what the book is and what object it becomes
2. **Specification** — write `MASTER_SPEC.md`, get it approved
3. **Research** — gather sources, then let them shape the outline
4. **Writing** — draft chapter by chapter against the spec
5. **Production setup** — cover, `book.yaml`, trim, typography
6. **Build** — compose and export
7. **Pre-flight and handover** — check, report, list what remains

Do not skip ahead. Phase 5 in particular has a hard dependency: **the cover
artwork comes from the author**. Never invent one, never generate one without
being asked, never leave it to the end.

## Phase 1 — Interview

Read `references/interview.md` and work through it. It is eight blocks of
questions, roughly forty-five in total, each with a default you can propose so
the author can answer "as you prefer" and move on.

Ask in batches of four to six related questions, not one at a time and not all
at once. Reflect what you heard back in one paragraph before moving to the next
block. If an answer implies a constraint the author may not have noticed — a
100,000-word manual in a 5×8 inch trim runs past 400 pages and the spine costs
more than the paper — say so once, in a sentence, and let them decide.

Four answers are load-bearing and cannot be defaulted silently:

- **language of the book**
- **paper, ebook, or both** — this decides everything about the files
- **trim size** — A4 is not a book format; if they say A4, ask what they mean
- **the cover** — who supplies the artwork, and does it exist yet

## Phase 2 — Specification

Write `MASTER_SPEC.md` in the project directory from
`templates/MASTER_SPEC.md`. It is the brief every later phase works against:
audience, thesis, structure with one line per chapter, voice, evidence
standard, apparatus, constraints, and the production decisions from Phase 1.

Show the author the chapter list and the word budget before writing a single
chapter. Revising an outline costs minutes; revising twenty drafted chapters
costs days.

## Phase 3 — Research

Read `references/research.md`. Research feeds two distinct things and both
matter:

- **the outline** — what a competent book on this subject has to cover, what
  the existing books get wrong or leave out, where the author's angle is
  actually new
- **the content** — the facts, studies, figures, cases and quotations inside
  each chapter

Use `WebSearch` and `WebFetch` (in Claude Code) or the equivalent tools in your
environment. Record everything in `research/` as you go, one file per topic,
each source with author, year, title, publication, DOI or URL, and the date you
read it. Tag every claim you intend to use with its evidence level, and never
let a number reach the manuscript without a source attached — reconstructing
citations after the fact is where invented references come from.

If the author declined research, say once what that costs (the book will assert
rather than demonstrate) and then write without it.

## Phase 4 — Writing

### Rule zero: one chapter, one file, written to disk before the next

**Write each chapter to `<project>/chapters/NN_name.md` the moment it is
drafted, using a real file-writing tool, before starting the next one.**

This is the single most important instruction in this skill, and it is the one
agents break most often. The failure is silent and it is total:

- You draft chapter 12 into your reply, your reasoning, a scratch buffer, a
  cache under a temp directory, or a "canvas" the runtime keeps for you.
- The session ends, is compacted, hits its context limit, or the user switches
  machine or agent.
- Chapter 12 no longer exists anywhere. There is nothing to recover, because
  nothing was ever written where the project lives.

A book is thirty to fifty chapters and many sessions. Anything you have not
written into the project directory does not exist. Treat your own context as
volatile storage — because it is.

Concretely, per chapter:

1. Draft it.
2. Write it to `<project>/chapters/NN_name.md` — full path, inside the project
   the user named, never a temp directory, never the skill directory.
3. Only then tell the user about it, and only then start the next chapter.

Do not batch. Do not write four chapters into one file intending to split them
later. Do not keep the last chapter "in hand" while you ask whether the user
likes it — write it first, then ask.

Before ending any working session, run `python -m bookgen status <project>`. It
lists what is on disk against what `MASTER_SPEC.md` plans, and it is the only
statement about progress you should trust. Never report a chapter as written on
the strength of remembering that you wrote it.

### Format

Files named so they sort correctly: `01_offerta.md`, `02_terminal.md`. Chapter
heading on the first line:

```markdown
# Capitolo 3: Title of the chapter — Optional subtitle
```

Follow the spec's structure and voice. Keep chapter length within ±15% of the
budget. Put per-chapter sources under a final `## Note bibliografiche` heading —
the composer lifts them out and merges them into one bibliography at the back.

Front and back matter go in `<project>/front/` and `<project>/back/`, one file
each, same rule: written to disk as soon as drafted.

### What must never reach the manuscript

Word counts, progress tables, "chapter complete" markers, TODOs, notes to the
author. They belong in your own tracking, not in the book. The composer strips
the obvious ones, but the reliable way to keep them out of print is not to write
them there.

## Phase 5 — Production setup

**Ask the author for the cover.** Three possibilities, and you need to know
which one applies before you build anything:

1. they have the artwork — get the file, check its resolution against the trim
2. they want one made — that is a separate commission; agree it explicitly
   before generating anything, and treat AI generation as a proposal, not a
   deliverable
3. they want type only — a typographic cover on a plain ground

For print at 300 dpi the artwork must be at least
`(trim width + 2×bleed) × (trim height + 2×bleed)` in millimetres × 11.81
pixels. A5 with 3 mm bleed means 1819 × 2551 px. Smaller than that is
enlargeable but never as good; tell the author the number rather than silently
upscaling.

Then write `book.yaml` from `templates/book.yaml`. See
`references/production.md` for trim sizes, margins, the spine calculation and
what each print channel demands.

## Phase 6 — Build

```bash
python -m bookgen build <project-dir>
```

The package lives in `scripts/` inside this skill directory. Add it to
`PYTHONPATH`, or run the command from there. It produces, in the project's `output/`:

- a print PDF at the exact trim, cover as page one plus its blank verso
- an interior-only PDF, which is what printers and KDP actually want
- cover files: with bleed for print, trimmed for the PDF page, sized for stores
- a DOCX with named styles, a live TOC field and STYLEREF running heads
- an EPUB 3 that passes KDP's requirements

Read `references/production.md` before changing composition defaults. The
non-obvious ones are load-bearing: chapters open on a recto, the page count is
padded to an even number, and the cover is followed by a blank so that every
recto stays a recto.

## Phase 7 — Pre-flight and handover

`bookgen build` runs the checks automatically; `python -m bookgen check <dir>`
re-runs them. They catch odd page counts, non-embedded fonts, text too close to
the trim, chapters landing on a verso, and EPUB manifest mismatches.

Then write `output/PRODUCTION_NOTES.md` and tell the author plainly what is
still open. There are almost always four:

- **ISBN** — placeholder until they register one, or omit it for KDP's free one
- **font licensing** — system fonts are fine to typeset with and not always
  fine to sell with; name the substitutes
- **spine and back cover** — the spine depends on the printer's paper; the back
  cover copy is the author's to write
- **anything the artwork could not deliver** — resolution, aspect ratio, colour

Report what the checks found, including what failed. A clean report on a book
you did not check is worse than no report.

## Reference files

| File | Read it when |
|---|---|
| `references/agents.md` | **Before Phase 4** — where your file tool actually writes, per runtime |
| `references/interview.md` | Phase 1 — the full question set |
| `references/research.md` | Phase 3 — sourcing, evidence levels, citation hygiene |
| `references/structure.md` | Phases 2 and 4 — front matter, back matter, chapter anatomy |
| `references/production.md` | Phases 5–7 — trim, margins, cover, print channels, checks |
| `references/typography.md` | Whenever composition defaults need changing |
| `references/troubleshooting.md` | When a build fails or a file is rejected |

## Templates

`templates/book.yaml`, `templates/MASTER_SPEC.md`, `templates/chapter.md`.
Copy them into the project with `python -m bookgen init <dir>`.
