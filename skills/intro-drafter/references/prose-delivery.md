<!-- Copy. Canonical lives at paper-writer/references/prose-delivery.md; edit the canonical,
     then re-sync. scripts/check_shared_sync.py enforces parity. -->

# Prose delivery discipline

The deliverable is clean prose the author can use as-is. Every planning
structure that produced it stays internal. This file defines what the user
receives and what must never appear in it.

## What the user receives

- The requested prose, in flowing paragraphs, with numeric citations `[1]`,
  `[2]` where sources are used.
- A **References** list immediately after the prose whenever the prose
  contains citation marks, numbered to match bidirectionally.
- Optionally, after the References: at most three lines of plain-language
  notes (an item needing the author's confirmation, an inference the author
  should check, a suggested next step). No tables, no lists, no headings. If
  there is nothing genuinely useful to say, omit the notes entirely.
- When the product is long (a full draft, multiple sections), write it to a
  file in the workspace and point the user to it; ledgers and maps go into
  separate working files, never into the prose.

## What must never appear in the output

- Markdown headings that name paragraphs ("Paragraph 1: Background").
- Type-positioning notes ("Type: Technique Paper").
- Consistency-check or gate results ("Running-example loop: pass").
- Severity summaries ("3 CRITICAL, 2 MAJOR").
- References to internal steps or frameworks ("Step 5 produced the
  following", "Based on the thinking template I built", "Following the CER
  framework").
- Process preambles, in any language: "Let me first analyze...", "Here I will
  structure...", "首先让我分析一下...", "根据蓝图...". Start directly with
  the prose.
- Bracketed placeholder tags of any kind: "citation needed", "to be
  verified", "to be specified by authors", "to be confirmed", "user
  provided", "model inference", and all variants.
- Emoji, decorative bolding, and alert boxes.

These are scaffolding: they are how the work gets thought, not what the
author should read. On catching yourself writing one, delete it and continue
with the prose.

## Formulas, tables, and emphasis in paper prose

- Display equations in `$$...$$`, inline math in `$...$`; never bare
  subscripts or HTML tags. Symbol explanations after an equation must use
  exactly the symbols the equation uses.
- Structured data (results, ablations, descriptive statistics) goes in a
  table, not in running prose. Tables carry a caption, headers, and units;
  in Draft mode, value cells without confirmed numbers hold `--` or an
  explicit PLANNING DATA marker, never invented values.
- Paper prose does not use markdown bold or italics for emphasis; carry
  emphasis in topic sentences or, where the venue allows, a subsection
  heading.

## Chunked delivery

If length forces chunking, label every chunk ("part i of N") and make each
chunk complete and usable on its own. Never deliver a partial product
presented as complete.
