# Running this from any agent

The method in `SKILL.md` is agent-independent, and the composer is a plain
Python package with a command-line interface. Hermes, Claude Code, Codex,
Antigravity, a shell script or a person can drive it identically.

What differs between runtimes is exactly one thing, and it is the thing that
ruins books: **where your file-writing tool actually writes.** Everything below
is about establishing that, once, before drafting a single chapter.

---

## The failure this page exists to prevent

An agent drafts twelve chapters over a long session. They are excellent. They
exist in the reply stream, in a reasoning buffer, in a runtime cache under a
temp directory, or in an attachment the interface renders nicely.

The session ends. Context is compacted. The user comes back tomorrow, or from a
different machine, or with a different agent.

Nothing is recoverable. Not by you, not by the next agent, not by the user.
There is no draft to fall back on, because a draft is a file and no file was
written.

This is not a hypothetical: it is the most common way a book project dies.
The defence is mechanical and costs nothing.

**Write the chapter to `<project>/chapters/NN_name.md` before you write a word
about it in your reply.**

---

## Establish the working directory first

Before Phase 4, run once:

```bash
python -m bookgen status <project>
```

It prints the absolute path it resolved. Check that it is the directory the
user meant — not a temp folder, not the skill directory, not your sandbox.

Then write one throwaway file there with your normal file tool and run `status`
again. If the file does not appear in the listing, your tool is not writing
where you think it is, and every chapter you draft from now on will be lost.
Fix that before continuing; it is not a detail you can work around later.

## The per-chapter loop

For each chapter, in this order:

1. draft it
2. **write it** to `<project>/chapters/NN_name.md`
3. report it to the user
4. next chapter

Never batch. Never keep the newest chapter "in hand" while asking whether the
user likes it — write it, then ask. If the user wants changes, you edit a file
that exists instead of regenerating one that does not.

Every four chapters, run `status`. It costs a second and it is the only
statement about progress worth trusting: your memory of having written
something is not evidence that a file exists.

At the end of every working session, run `status` a final time and report its
output verbatim. If it lists missing chapters, say so plainly and rewrite them
now, while the drafts may still be in context. Do not close a session on an
unreported gap.

---

## Hermes

Hermes is the primary target for this skill and has two habits worth naming.

**Where it writes.** Hermes runs with a working directory that is not
necessarily the user's project. Always pass the **absolute path** to both the
file-writing tool and to `bookgen`:

```
python -m bookgen status /absolute/path/to/project
```

Never rely on a relative path or on `.` unless you have just verified with
`status` that `.` resolves where you expect.

### Installing it on Hermes

Hermes reads skills from `HERMES_HOME/skills`, which on Windows is
`%LOCALAPPDATA%\hermes\skills` — **not** the `hermes-agent` repository
directory next to it, which is a common and silent mistake. Confirm it:

```bash
<hermes>/venv/Scripts/python -c "from tools.skills_tool import SKILLS_DIR; print(SKILLS_DIR)"
```

Then link the skill package into it, so edits to the source propagate:

```powershell
New-Item -ItemType Junction `
  -Path "$env:LOCALAPPDATA\hermes\skills\book-forge" `
  -Target "<source>\skills\book-forge"
```

Three things to know once it is in place.

**Keep only one copy.** Hermes scans the tree and refuses to choose between two
`SKILL.md` files with the same name — it reports a *skill name collision* and
loads neither properly. Backups of an older version must live **outside**
`skills/`, not renamed inside it: a leading dot does not exclude them.

**It runs on its own interpreter.** Hermes uses `<hermes>/venv`, which is
Python 3.11 and separate from the system Python. The composer's dependencies
must be installed *there*:

```bash
<hermes>/venv/Scripts/python -m pip install -r <skill>/scripts/requirements.txt
```

A skill that loads but fails at build time is almost always this.

**Expect a trust warning.** Loading through a junction that points outside
`~/.hermes/skills/` prints *"skill file is outside the trusted skills
directory"*. It is a warning, not a refusal: the skill loads and runs normally.
To silence it, copy the package in instead of linking it — at the cost of
having to re-copy after every edit.

**Long-running work.** For a forty-five chapter book Hermes will run for many
turns and may summarise its own context along the way. Anything summarised is
anything lost. Write each chapter to disk as it is drafted — a summary of a
chapter is not a chapter — and keep `MASTER_SPEC.md` on disk and re-read it
rather than remembering it.

If Hermes has no direct Python execution, it can still run the CLI through its
terminal capability — that is all the composer needs. No interactive prompts,
no network access, and meaningful exit codes: `0` success, `1` configuration
problem, `2` missing cover artwork, `3` a failed check or missing chapters.

---

## Claude Code

The skill is discovered automatically when installed under
`~/.claude/skills/book-forge`. Use `Write` for chapters — one call per chapter,
absolute path — and `Bash` for `bookgen`.

Claude Code compacts long conversations. The compaction preserves a summary,
not the text of your chapters. Same rule, same reason.

---

## Codex

Register the skill under `~/.codex/skills/book-forge` and add a pointer in
`~/.codex/AGENTS.md`. Codex runs shell commands directly, so the CLI needs no
adaptation. Verify the sandbox writes to the real project path before drafting.

---

## Anything else

The contract is small enough to give to any runtime:

- **project layout**: `book.yaml`, `MASTER_SPEC.md`, `chapters/NN_name.md`,
  `front/`, `back/`, `research/`, `output/`
- **chapter format**: markdown, first line `# Capitolo N: Title — Subtitle`
- **commands**: `init`, `status`, `build`, `check`, `info`
- **the rule**: one chapter, one file, written before the next begins

Python API, if the CLI is inconvenient:

```python
import sys; sys.path.insert(0, "<skill>/scripts")
from bookgen import load_config, build_book
from bookgen.pdf import build_pdf

cfg = load_config("/path/to/project")
info = build_pdf(build_book(cfg), cfg.out_dir / "book.pdf", cfg)
```

## Handing a project to another agent

A project directory is self-describing: `MASTER_SPEC.md` says what the book is,
`book.yaml` says what object it becomes, `chapters/` holds what exists, and
`status` reports the gap. Nothing lives in an agent's memory, which is the whole
point of writing everything down.

Tell the next agent three things: the project path, to read `MASTER_SPEC.md`
first, and to run `status` before writing anything.
