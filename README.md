# bookwriter

Turn scattered notes — or entire books — into an ordered **book** or **archive**, using a **local
LLM** served by **LM Studio**. Organization and writing **style** emerge from a conversational
workflow. Output as a print-ready **PDF / DOCX / EPUB / Markdown**, or a navigable **wiki archive**
(HTML + graph).

Everything runs locally: your text never leaves your machine.

Full design document: [`docs/PLAN.md`](docs/PLAN.md).

---

## What it does

Four ways to produce a book from the same engine:

| Mode | From → To | Command |
|---|---|---|
| **Compose** | scattered fragments → book/archive (verbatim *or* rewritten prose) | `write` / `organize` + `compose` |
| **Rewrite** | a whole book → same book in a new **style** | `rewrite` |
| **Translate** | a whole book → another **language** (style preserved) | `translate` |
| **Forge** | (almost) nothing → a **finished, print-ready volume**, through an author interview | `forge` |

Plus: four **orderings** (thematic / chronological / narrative / custom), **chapters → sections →
nodes**, a **style engine** (extract / describe / imitate authors with weights), **word-count
targets** with budgeting, a **global coherence** pass (names, timeline, repetitions), persistent
**intents** for multi-session continuity, and a **PySide6 desktop GUI**.

### One typesetter, not several

Every screen that produces a new book goes through the same composer
(`bookwriter/forge/bookgen`, the [book-forge](https://github.com/) engine vendored into the
package). Whether the text comes from an outline, a rewrite, a translation or the phases,
it is set the same way: **real trim size** instead of A4, asymmetric margins that account for the
gutter, hyphenation, chapters opening on a recto, running heads, a contents page, and per-chapter
sources merged into one bibliography. Output as **PDF · DOCX · EPUB · Markdown**.

The one exception is deliberate. When you translate or rewrite a book that was *imported*,
`bookwriter/layout` puts the new text back inside the original DOCX, leaving fonts, margins,
headers, images and tables untouched. Composing a new book and preserving an old book's layout
are two different jobs, and the second one is not done with a typesetter.

---

## Requirements

- **Python 3.10+**
- **[LM Studio](https://lmstudio.ai/)** running at `http://localhost:1234` with two models loaded:
  - a **chat / reasoning** model (any instruct model; the default `qwen3-8b-instruct`
    is only a starting value, changeable in the Settings tab)
  - an **embedding** model (default: `text-embedding-nomic-embed-text-v1.5`)
- No system libraries needed: PDF via `reportlab`, DOCX via `python-docx`, EPUB built in-package,
  covers via `pillow`, hyphenation via `pyphen`, print checks via `pymupdf`.

## Installation

### From the distributed .zip (Windows, recommended)

Unpack the archive, open PowerShell in the extracted folder and run:

```powershell
.\install.ps1      # finds Python 3.10+, creates .venv, installs everything, checks LM Studio
.\run.ps1          # opens the desktop application
```

`install.ps1` downloads about 1.5 GB of dependencies the first time (PySide6 and LanceDB are the
bulk of it). You also need a model service running: **LM Studio** on `localhost:1234`, or a cloud
provider key set in the Settings tab.

Your projects and books are stored in `~/.bookwriter/`, outside the program folder: updating to a
newer version never touches them.

The full **installation and user manual** is in `docs/bookwriter-manual.pdf` (source:
`docs/MANUAL.md`).

### From source, manually

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Git Bash:            source .venv/Scripts/activate

pip install -e ".[all,dev]"     # everything: EPUB import + GUI + dev tools
# or minimal:
pip install -e .                # core: memory, the phases, PDF/DOCX/EPUB output,
                                # PDF and DOCX import — no GUI, no EPUB import
pip install -e ".[ingest]"      # + import EPUB
pip install -e ".[gui]"         # + desktop GUI
```

Check your setup:

```bash
bookwriter doctor               # lists loaded models, verifies roles, runs a chat + embed test
```

---

## Quickstart

### A. Compose — from fragments to a book

```bash
bookwriter project new "My book" --target-words 30000
bookwriter ingest ./notes --recursive            # files or whole folders (dedup, checkpointed)
bookwriter atomize                               # split chunks into idea-nodes (themes/entities/dates)
bookwriter memory graph                          # embed nodes + build the thematic graph
bookwriter organize --ordering thematic          # cluster nodes into chapters
bookwriter outline sections                      # (optional) split chapters into sections

# Verbatim anthology (reorder originals, no rewriting):
bookwriter export --format docx                  # or md / pdf

# OR narrative prose (rewrites nodes in a chosen style, hits the word target):
bookwriter compose --style my_style --coherence --format pdf
```

### B. Rewrite — a book in a new style

```bash
bookwriter project new "Restyle" && bookwriter ingest ./book.txt
bookwriter style abstract "Terse, hard-boiled, short sentences." --name noir
bookwriter rewrite --style noir --preview        # preview chapter 1 (original ↔ rewritten)
bookwriter rewrite --style noir --format docx    # whole book, deterministic, resumable
```

### C. Translate — keep the style, change the language

```bash
bookwriter translate --to English --preview
bookwriter translate --to English --format pdf   # bilingual glossary keeps names consistent
```

### D. Forge — write from scratch, in eight phases

You talk the book through with the assistant — a chat, not a questionnaire — and then it makes
it. The conversation is about the book itself: what you want it to achieve, the reader, the plot
and the chain of events, how facts connect, the characters and places, the scenes you want and
the ending. The assistant asks, proposes and discusses until you agree; printing and the cover are
decided by hand in production, and the style comes from the style drop-down. When you close the
conversation, the assistant decides the rest and says what it decided.

1. **Interview** — the conversation, and the book notebook it fills
2. **Specification** — write `MASTER_SPEC.md`, get it approved
3. **Research** — gather sources, then let them shape the outline
4. **Review** (optional) — discuss the findings, inconsistencies and weaknesses with the
   assistant, one point at a time; you decide, the book keeps your voice
5. **Writing** — draft chapter by chapter against the spec
6. **Production** — cover, `book.yaml`, trim, typography
7. **Build** — compose and export
8. **Checks and handover** — verify, report, list what remains

```bash
bookwriter project new "From zero"
bookwriter forge init
bookwriter forge interview            # a chat; /quaderno shows the notebook, /fine closes it
bookwriter forge finish               # the assistant completes the notebook and decides the rest
bookwriter forge spec                 # proposes the structure, writes MASTER_SPEC.md
bookwriter forge approve              # revising an outline costs minutes, twenty chapters cost days
bookwriter forge research --chapters  # the landscape first, then the chapters' sources; resumes if interrupted
                                      # (--redo keeps the material and redoes it; --fresh sets it aside and starts over)
                                      # blocked pages (CAPTCHA…): see docs/browser_integration.md
bookwriter forge write --all          # each chapter hits the disk before the next one starts
bookwriter forge produce --cover art.jpg
bookwriter forge build                # print PDF + interior + covers + DOCX + EPUB, then pre-flight
bookwriter forge notes                # what is still open: ISBN, font licences, spine, back cover
bookwriter forge status               # what is on disk against what the spec plans
```

Starting over without losing the book:

```bash
bookwriter forge save "before the rewrite"  # one zip, copy it anywhere
bookwriter forge saves                      # what has been put aside, newest first
bookwriter forge restore 1                  # back to work on it; the current book is saved first
bookwriter forge reset                      # back to the interview, the current book put aside
bookwriter forge reset --delete             # …or thrown away, if that is what you mean
```

The same three actions sit at the bottom of the **Write from scratch** tab. Restoring is never
destructive: whatever is in the folder is put aside before it is replaced, so a wrong click costs
a click, not a book.

> **Rule zero.** Every chapter is written to `<project>/book/chapters/NN_name.md` the moment it is
> drafted, before the next one begins. A book spans many working sessions; a model's context does
> not. Whatever is not on disk when a session ends cannot be recovered — not by the model, not by
> another assistant, not by you. `forge status` reads the file system, and it is the only progress
> report worth trusting.

Two things the system will not do: **invent cover artwork** (the author supplies it; you get told
the pixel size the trim requires instead of a silent upscale) and **fabricate citations** (no
figure or quotation reaches the manuscript unless its source is already written in `research/`).

The book lives in a self-describing folder, so another assistant — or you, alone, in a text
editor — can pick it up knowing only the path:

```
<project>/book/
  book.yaml         trim, metadata, cover, output formats
  MASTER_SPEC.md    the brief every phase works against
  chapters/         one markdown file per chapter, named so it sorts
  front/  back/     introduction, notice, glossary, author biography
  research/         the sources, with the evidence level of every claim
  output/           everything the build produces
```

**Research goes online.** Phase 3 can search the web for the books that already exist and for the
chapters' sources — the only part of the program that leaves your machine. It ships **off**; turn
it on in Settings and pick an engine (DuckDuckGo needs no key; Brave, Tavily and your own SearXNG
instance are also supported).

---

## Key concepts

- **Nodes** are atomic ideas. They live in the project's memory (SQLite for metadata/graph,
  LanceDB for embeddings). A node never "belongs" to a chapter — chapters are just *paths* over the
  nodes, so the **same nodes yield different books**.
- **Orderings**: `thematic` (clustering), `chronological` (by extracted dates), `narrative` (an arc
  built by the LLM), `custom` (`--criterion "by character"` — the LLM applies your rule).
- **Rendering**: `verbatim` (reorder original excerpts, high fidelity) or `narrative` (new prose in
  your style, hitting the word target).
- **Style profiles** (9 dimensions: syntax, lexicon, rhythm, voice, tone, …):
  ```bash
  bookwriter style extract sample.txt --name mine        # from a text sample
  bookwriter style abstract "warm, essayistic" --name warm
  bookwriter style authors --spec "Calvino:0.7:syntax,rhythm" --spec "Hemingway:0.3:lexicon" --name blend
  bookwriter style extract sample.txt --name mine --global   # save to the reusable global library
  bookwriter style import mine                           # import a global style into a project
  ```
- **Continuity & intents**: interactive decisions (style, ordering, answers) are **saved in the
  project**, so you can resume across sessions:
  ```bash
  bookwriter ask                     # the LLM proposes questions to fill content gaps
  bookwriter answer 1 "..."          # your answer becomes a new node
  bookwriter session brief           # reconstructs the project state for a fresh session
  ```
- **Coherence pass** unifies names/terms across the whole book and reports timeline issues and
  repetitions: `bookwriter coherence`.

---

## Exports

- `--format md | docx | pdf` for any book (verbatim, composed, rewritten, translated).
- `bookwriter export --format wiki` → a self-contained **archive**: `wiki.html` (searchable node
  pages + an interactive **SVG graph**) and `graph.json` (nodes + edges).

---

## Desktop GUI

```bash
bookwriter gui
```

Thirteen panels over the same engine: import, prepare content (split into sections, index, connect),
compose the book, structure editing, writing style, complete the content, project summary, rewrite,
translate, write from scratch, update an existing draft, model usage and costs, settings. Long
operations run in the background with a progress bar and a live count.

See `docs/bookwriter-manual.pdf` for the full guide.

---

## CLI reference

```
doctor                         check LM Studio and models
project  new|list|switch|delete|info
ingest   <file|dir…> [-r] [--include] [--exclude]
atomize                        chunks → idea-nodes
memory   embed | search <q> [--nodes] | graph | stats
organize --ordering thematic|chronological|narrative|custom [--chapters N] [--criterion "…"]
outline  list | show | sections | (drag-and-drop editing in the GUI)
compose  [--style] [--target-words] [--coherence] [--format md|docx|pdf]
rewrite  --style <ref> [--preview] [--ratio R] [--format …]
translate --to <lang> [--style] [--preview] [--format …]
author   start | expand | draft
style    extract | abstract | authors | list [--global] | show | apply | import
spec     [--target-words N]    book length target (BookSpec)
coherence [--outline|--run]    unify names + report timeline/repetitions
export   --format md|docx|pdf|wiki
jobs                           history/progress of heavy operations
session  brief | intents
gui                            launch the desktop app
```

## Configuration

Per-workspace defaults in `~/.bookwriter/config.toml`, per-project overrides in the project folder.
Notable settings: LM Studio `base_url`, model roles (`reasoning`, `embedding`, optional `heavy` for
quality passes), `concurrency` (parallel LLM calls), chunking `target_tokens`, nomic embedding
prefixes.

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest -q       # unit tests use fake LLM clients (no network)
```

## Status

Feature-complete; all pipeline stages verified end-to-end against LM Studio. See the roadmap in
`docs/PLAN.md`.
