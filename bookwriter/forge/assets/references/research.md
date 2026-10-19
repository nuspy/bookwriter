# Research

Research serves two purposes in a book and they are not the same job. Doing
only the second is how you get a well-sourced book that covers the wrong
things.

## Before the outline: what the book must contain

Do this in Phase 3, before the chapter list is fixed. You are answering four
questions.

**What does a competent book on this subject have to cover?** Find three to
five existing books and read their tables of contents — they are usually
visible on the store page. The union of their chapters is the field's implicit
syllabus. Anything that appears in all of them and not in your outline needs a
deliberate reason to be absent.

**What do the existing books get wrong or skip?** Read the critical reviews,
not the praise. Recurring complaints — "repetitive after chapter 4", "no
practical examples", "outdated on X" — are a specification for what this book
should do differently.

**What has changed recently?** Search for work from the last two or three
years. A subject where the last synthesis is a decade old is an opportunity; a
subject with a definitive book from last year needs a different angle.

**Where is the author's contribution actually new?** Be honest with them about
this. If the research shows the ground is covered, say so — that conversation
belongs before the writing, not after.

Write the result to `research/00_landscape.md`: the competing titles, their
structures, the gaps, and the outline consequences. Then revise the chapter
list before showing it to the author.

## During writing: what goes inside the chapters

One file per topic in `research/`, written as you go, never reconstructed
afterwards. For each source record: author, year, title, publication or
publisher, DOI or URL, the date you consulted it, and — separately — what it
actually says, in your own words, with the specific figures.

The rule that prevents the worst outcome: **no number, no claim of fact and no
quotation enters the manuscript without its source already written down.**
Citations invented at the end of a writing session look exactly like real ones
and are the single most damaging thing you can put in a book.

### Evidence levels

Tag every substantive claim. The tag stays in your notes; the text carries the
citation.

| Level | What it is | How to write it |
|---|---|---|
| A | Meta-analysis, systematic review, replicated experiment | State it plainly, cite |
| B | Single peer-reviewed study, official statistics | Attribute: "a 2024 study of 300 participants found…" |
| C | Reputable secondary source, professional consensus, book by a recognised authority | Attribute and hedge |
| D | Case, interview, personal experience, illustrative anecdote | Mark it as such; never generalise from it |
| E | Speculation, extrapolation, the author's model | Say it is the author's view |

The failure mode to watch: a level-D anecdote written with level-A confidence.
It reads well and it is false.

### Numbers deserve suspicion

Percentages that travel through popular articles detach from their source and
grow. Chase every figure to the paper that produced it. Check what was actually
measured, on whom, and whether the effect is what the sentence implies. When
the underlying evidence is thin, either write the sentence without the number
or state the uncertainty — "the effect is small and the studies disagree" is
publishable; a confident fake decimal is not.

### Quotations

Quote sparingly and exactly, with a locatable reference — page, chapter or
section. For anything under copyright keep quotations short and clearly
attributed; song lyrics and contemporary poetry usually require permission for
any length at all. Do not reconstruct a quotation from memory: find it or drop
it.

## Tools

In Claude Code: `WebSearch` for finding, `WebFetch` for reading the source
itself. Read the primary source when it exists rather than a summary of it.
Other environments: whatever equivalent is available; the discipline matters
more than the tool.

Consider the Consensus MCP tool if it is connected — it searches the scientific
literature directly and returns papers with citation counts.

## Output

- `research/00_landscape.md` — competing titles, gaps, outline consequences
- `research/NN_topic.md` — one per subject area, with sources and findings
- `research/sources.md` — the running bibliography, one entry per source,
  in the citation style chosen in the interview

The composer merges per-chapter reference lists into one bibliography
automatically; `research/sources.md` is your working copy and can hold sources
that were consulted but not cited.

## When the author declined research

Say once, plainly, what it costs: the book will assert rather than demonstrate,
and any figure in it will be the author's responsibility rather than the
literature's. Then write it that way — no invented citations, no borrowed
authority, no statistics. A well-argued book with no sources is honest. A book
with fabricated ones is not recoverable.
