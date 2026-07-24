# bookwriter

Turn scattered notes — or entire books — into an ordered **book** or **archive**, using a **local
LLM** served by **LM Studio**. Organization and writing **style** emerge from a conversational
workflow. Output as **DOCX / Markdown / PDF** or a navigable **wiki archive** (HTML + graph).

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
| **Authoring** | (almost) nothing → a full draft, **created via dialogue** | `author` |

Plus: four **orderings** (thematic / chronological / narrative / custom), **chapters → sections →
nodes**, a **style engine** (extract / describe / imitate authors with weights), **word-count
targets** with budgeting, a **global coherence** pass (names, timeline, repetitions), persistent
**intents** for multi-session continuity, and a **PySide6 desktop GUI**.

---

## Requirements

- **Python 3.10+**
- **[LM Studio](https://lmstudio.ai/)** running at `http://localhost:1234` with two models loaded:
  - a **chat / reasoning** model (default: `gemma-4-12b-obliterated`)
  - an **embedding** model (default: `text-embedding-nomic-embed-text-v1.5`)
- No system libraries needed: DOCX via bundled Pandoc (`pypandoc-binary`), PDF via `reportlab`.

## Installation

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Git Bash:            source .venv/Scripts/activate

pip install -e ".[all,dev]"     # everything: PDF/DOCX loaders + GUI + dev tools
# or minimal:
pip install -e .                # core (txt/md, memory, DOCX/PDF export) — no GUI, no pdf/docx import
pip install -e ".[ingest]"      # + import PDF / DOCX / EPUB
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

### D. Authoring — write from scratch by talking

```bash
bookwriter project new "From zero" --target-words 5000
bookwriter author start --theme "Space exploration" --audience "curious teens" --goal "inspire wonder"
bookwriter author expand --topic "the planets" -n 6      # the LLM generates idea-nodes
bookwriter author expand --topic "astronauts" -n 6
bookwriter author draft --format md                      # graph → organize → prose → export
```

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

Ten panels over the same engine: Projects (with live word counter), Ingestion (drag-and-drop),
Memory + semantic search (chunks or nodes), Organize & Export (+ narrative composition), Outline
(drag-and-drop editing, split into sections), Style, Rewrite, Translate, Interaction (questions /
intents), Brief, Authoring. Long operations run in the background with a dedicated progress bar.

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
