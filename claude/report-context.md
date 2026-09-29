# FYP Report — Editing Context (as of 2026-09-29)

Read this first before touching `FYP_Report_Updated.docx` in any new session.
This file documents the **Word report specifically** — it is separate from
`claude/project-context.md`, which documents the codebase/hardware/software
project as a whole. Do not confuse the two.

## 1. Standing instruction — do not commit or push this file

The user explicitly said **"no need to commit and push this"** regarding
`FYP_Report_Updated.docx` edits. Treat that as still in force for all future
edits to the docx unless the user says otherwise in a given session. This
markdown file itself is a normal repo file and can be committed/pushed if
the user asks — the "no commit" instruction is about the report content,
not about this documentation.

## 2. What this document is

`C:\OutdoorKoiFYP\FYP_Report_Updated.docx` is the live final-year-project
report, edited directly and programmatically via `python-docx` (not by hand
in Word). As of this writing: **706 paragraphs, 30 tables, 3 inline images**.

Editing is done with small one-off Python scripts (not a persistent tool in
the repo) written to a scratchpad directory per session and run via the `py`
launcher (only `py`, not `python`/`python3`, has `python-docx` installed on
this machine — check with `py -c "import docx"` at the start of a new
session rather than assuming).

## 3. Chapter structure and paragraph-index map

Chapter headings and their current `d.paragraphs` index (0-based, from
`python-docx`'s flat paragraph list):

| Index | Chapter |
|---|---|
| 8 | Chapter 1: Introduction |
| 36 | Chapter 2: Literature Review |
| 148 | Chapter 3: Architecture and System Design |
| 321 | Chapter 4: Experimental Framework |
| 416 | Chapter 5: Software Implementation and Verification |
| 495 | Chapter 6: Exploiting NEA Telemetry and Forecasts for Preemptive Pond Management |
| 563 | Chapter 7: Results |
| 578 | Chapter 8: Discussion and Conclusion |

Chapter 6 is a **new chapter**, integrated from
`NEA_Data_Analysis/nea_final_reports/UNIFIED_NEA_CHAPTER.md` (the source
markdown, still git-tracked, x.1–x.11 numbering in that file maps to 6.1–6.x
in the docx). The old Chapter 6 (Results) and Chapter 7 (Discussion/
Conclusion) were renumbered to 7 and 8 to make room. Section 1.4 (the
Introduction's own chapter-by-chapter outline, around paragraphs 30–35)
was updated to match.

**Indices drift on every structural edit** (insert/delete of a paragraph, or
adding/removing a table, which is a separate body-level XML element not
counted in `d.paragraphs`). **Never hand-transcribe a paragraph index from a
Read-tool line number or from a previous session's notes** — this caused two
real mistakes during the Chapter 2 pass (mislabelled paragraph 87→122 and
90→125). Always re-derive the current index at the start of a session by
searching `d.paragraphs` for a unique text snippet:

```python
import docx
d = docx.Document(r"C:\OutdoorKoiFYP\FYP_Report_Updated.docx")
for i, p in enumerate(d.paragraphs):
    if "some unique phrase from the target paragraph" in p.text:
        print(i, p.text[:120])
```

## 4. Style rules in force (whole document)

These were applied document-wide during the Chapter 6 integration pass and
must be preserved in any future edit:

- **Zero em-dashes** (U+2014) and **zero semicolons** anywhere, including
  inside table cells. Verify with a full-document scan after any edit:
  ```python
  em = sum(1 for p in d.paragraphs if "\u2014" in p.text)
  semi = sum(1 for p in d.paragraphs if ";" in p.text)
  # + the same loop over d.tables / row.cells / cell.text
  ```
  Both must print 0.
- **No short, dramatic one-line sentences** used for rhetorical effect (e.g.
  "That claim is narrower than the project initially assumed."). Ordinary
  short sentences that are genuinely just short (table captions, section
  labels, reference-list entries, technical one-liners like "Settling
  time.") are fine and expected — don't hunt those down as if they were
  the same problem.
- **Numerals follow APA 7th-edition convention**: spelled-out number words
  are converted to digits when the value is ≥10, OR when the value is
  attached to a unit/measurement/time regardless of magnitude (e.g. "two
  hours" → "2 hours", "three fish" stays as words if <10 and unitless
  narrative). The converter used for this is `numconv2.py` (built fresh
  each session in the scratchpad; not committed to the repo). If rebuilding
  it, the known edge cases that broke earlier attempts are worth avoiding
  again:
  - "point" must only be consumed as a decimal separator when a number word
    actually follows it (otherwise "two-point calibration" becomes "2
    calibration").
  - "and a half" must be detected as an explicit lookahead after a run of
    number words, not assumed to already be inside the run.
  - A bare scale word alone ("a hundred", "a million") used idiomatically
    should not be converted ("a quarter of a million requests" must not
    become "a quarter of a 1000000 requests").
  - **Never run the converter across quoted source material.** It was run
    document-wide without excluding quotation marks in an earlier pass, and
    it altered one direct quotation (paragraph 54, Diana et al. 1997: "as
    much as 10°C during one day" had been silently changed to "during 1
    day" — since fixed). A document-wide check for other instances came
    back clean (`grep`-style scan for quoted spans containing a lone digit
    beside a unit word — see §6 for the check script), but re-run that
    check after any future numeral pass. Quoted text must be reproduced
    exactly as sourced; APA numeral style does not override that.
- **Reduce redundancy/verbosity** where the same point is made multiple
  times in close proximity — condense rather than just trim words.
- General academic-editing concerns applied on close-reading passes (not
  just the four mechanical rules above): sentence fragments, comma splices,
  subject-verb agreement, missing words, first-person "we" (register should
  stay third-person/passive-academic, not first-person), citations used
  ungrammatically as nouns ("said paper"), and closing-punctuation
  convention for quotations (period goes **inside** the closing quotation
  mark: `"...for farmers."` not `"...for farmers".`).

## 5. Editing status per chapter

| Chapter | Mechanical pass (numerals/dash/semicolon/renumbering) | Deep close-read (grammar/clarity/redundancy) |
|---|---|---|
| 1 Introduction | Done | Not done |
| 2 Literature Review | Done | **Done — 3 rounds** (see below) |
| 3 Architecture | Done | **Done** (see below) |
| 4 Experimental Framework | Done | **Done** (see below) |
| 5 Software Implementation | Done | **Done** (see below) |
| 6 NEA Telemetry (new) | Done (written to this standard from the start) | Done as part of writing it |
| 7 Results | Done | Not done |
| 8 Discussion/Conclusion | Done | Not done |

Chapter 2 received a dedicated close-reading pass across three rounds:
round 1 fixed ~15 issues (fragments, comma splices, subject-verb agreement,
"fishes"→"fish", stray non-breaking spaces, register fixes). Round 2 fixed
9 more issues caught on a second read (informal "said paper" phrasing,
quote-then-period punctuation ordering, a repeated word, a missing noun,
verbose hedging, comma splices in bullet points). Round 3 (a third
close-read, done specifically to check whether two passes were enough)
caught and fixed the quote-integrity bug described in §4 above, and
confirmed via a document-wide scan that no other quotation was altered by
the numeral pass.

Chapter 3 received one close-reading pass, which fixed 12 issues: three
sentences that started with a coordinating conjunction ("And ..."),
normalised for register consistency; one sentence that started with a bare
numeral ("5 minutes gives..."), which violates the APA convention against
opening a sentence with a digit; a missing word ("wake just early" →
"wake a little early"); two instances of a scare-quoted phrase followed by
a comma placed outside the closing quotation mark, corrected to match the
period-inside-quote convention established in Chapter 2; and five missing
commas (a relative clause that produced a garden-path/run-on sentence, a
conditional clause, two compound-independent-clause joins, and a leading
adverbial phrase) — the comma-before-conjunction pattern was already
consistent everywhere else in the chapter, so these read as isolated
omissions rather than a different convention.

Chapter 4 received one close-reading pass, which fixed 4 issues: a missing
comma before "and" joining two independent clauses (same pattern as
Chapter 3); a subject-verb agreement error ("If T1 or T2 fail" → "fails" —
singular subjects joined by "or" take a singular verb); an ambiguous
unhyphenated phrase ("2 day and night cycles" read as "2 day" + "night
cycles", fixed to "2 day-and-night cycles"); and an inconsistent list
punctuation style in a 3-item compound predicate, normalised to the Oxford
comma convention established in Chapter 2. A systematic scan for the other
known failure patterns (sentence-initial "And", sentence-initial numerals,
quote-then-comma ordering) came back clean for this chapter.

Chapter 5 received one close-reading pass, which fixed 4 issues: three more
instances of a sentence starting with "And" (the same register-consistency
pattern found in Chapter 3 — it did not recur in Chapter 4, but its
reappearance here means it's worth specifically checking for in any
remaining chapter's deep pass, not assuming it was a one-off), and one
quote-then-comma ordering issue, corrected to match the
comma/period-inside-closing-quote convention established in Chapters 2
and 3.

**Do not treat "done" mechanical passes on Chapters 1, 7, 8 as equivalent
to Chapters 2/3/4/5's deep pass.** Those chapters have only had the
numeral/dash/semicolon/renumbering sweep, not a close read for genuine
grammar and redundancy issues. The user has been directing this
chapter-by-chapter — don't proceed to another chapter's deep pass without
being asked, per established pattern. When one is requested, it's worth
grepping for `(?:^|[.!?]\s+)(And [a-z])` early — it has hit in 2 of the 3
chapters checked so far (3 and 5, not 4).

## 6. Verification checklist after any future edit

Run this after any batch of edits, before considering the task done:

```python
import docx
d = docx.Document(r"C:\OutdoorKoiFYP\FYP_Report_Updated.docx")
p = d.paragraphs
em = sum(1 for x in p if "\u2014" in x.text)
semi = sum(1 for x in p if ";" in x.text)
for t in d.tables:
    for row in t.rows:
        for cell in row.cells:
            if "\u2014" in cell.text: em += 1
            if ";" in cell.text: semi += 1
print("em-dash:", em, "semicolon:", semi)  # both must be 0
```

Also worth re-running after any numeral-conversion pass — scan quoted spans
for a suspicious lone digit next to a unit word (catches the paragraph-54
class of bug):

```python
import re
suspects = re.compile(r'\b1 (day|days|year|years|month|months|hour|hours|week|weeks|minute|minutes|second|seconds|time|times)\b')
for i, para in enumerate(p):
    for m in re.finditer(r'"[^"]{0,300}"', para.text):
        if suspects.search(m.group(0)):
            print(i, m.group(0))
```

## 7. Other relevant files

- `NEA_Data_Analysis/nea_final_reports/UNIFIED_NEA_CHAPTER.md` — the
  markdown source Chapter 6 was integrated from. Committed to git. If the
  underlying NEA analysis changes, this file and the docx Chapter 6 will
  need to be re-synced manually (no automated link between them).
- `NEA_Data_Analysis/forecast_reality_pipeline.ipynb` — the canonical
  notebook behind all NEA validation claims in Chapter 6 (§0–§8:
  permutation controls, residual skill tests, streak checks, the
  regime-aware `HOT_THR` fix). Committed to git.
- `claude/project-context.md` — the whole-project (codebase/hardware)
  context doc, last updated 2026-08-20. Read that one for questions about
  the software/hardware system itself, not the report.
