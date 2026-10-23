# Delivery verification checklist

## Table of contents

1. Step 1: source recall check
2. Step 2: inline checks (literature, facts, evidence matching,
   cross-section, AI-trace, formulas and tables, delivery format)
3. On failure

Run before delivery. Full papers walk every item explicitly; single sections
or paragraphs run the same pass mentally.

## Step 1: source recall check

Go through the draft claim by claim and ask of each factual statement: where
did this come from? The only acceptable answers are the user's materials, this
session's retrieval results, or L0 field common knowledge (no numbers, names,
or comparisons).

- A claim you cannot trace to one of those three origins is presumed to be
  model memory: delete it, rewrite the sentence to drop the claim, or search
  for a real source now.
- Do not resolve anything by inserting a bracketed annotation; the resolution
  is always search, rewrite, or delete.

## Step 2: inline checks

### 2.1 Literature

> Citation **existence and metadata** are owned by the independent
> verification pass when it triggers (full paper, Final mode, or 3 or more
> citations; the trigger table in the verification ladder is authoritative).
> The three items below serve as the inline fallback for light fragments;
> when the independent pass ran, its statuses win. Everything else in Step 2
> is always checked inline.

- [ ] Every in-text citation mark traces to the user's materials or this
      session's retrieval?
- [ ] No reference generated from model memory?
- [ ] No invented methodological descriptions of cited work (a review cited
      as a "meta-analysis")?
- [ ] In-text marks present where sources are used (not a bare reference
      list with uncited entries)?
- [ ] References section printed, numbered to match the text bidirectionally?
- [ ] Each entry carries author, title, venue, year (unconfirmable entries
      dropped; nonessential missing fields omitted)?
- [ ] Citation format consistent (numeric citation-sequence by default, or
      the user's requested style)?

### 2.2 Facts

- [ ] Institutional details (policy rules, program mechanics) all from user
      input, none supplemented?
- [ ] Case citations and statute numbers only from user input, never from
      memory?
- [ ] Scientific comparison values sourced?
- [ ] No invented statistics (covariate p-values, aggregation indices)?
- [ ] No invented procedural detail: scale item counts, anchors, consent
      procedure, software versions, administration dates, missing-data
      rates, implementation parameters?
- [ ] No application scenarios the user never mentioned?
- [ ] No mechanisms, physical principles, or causal explanations the user
      never described?
- [ ] No magnitude or scale descriptions the user never gave (hundreds of,
      fewer than ten, tenfold, order of magnitude)?

### 2.3 Evidence matching

- [ ] Every strong judgment has evidence or an explicit boundary?
- [ ] No metadata-level (L3) source written up as a full-text-level claim?
- [ ] User data presented according to its nature (real results versus
      expected results)?
- [ ] Contribution claims traceable to an experiment or figure?
- [ ] Draft-mode planning data confined to tables and marked as planning
      data?

### 2.4 Cross-section

- [ ] Discussion explains why, without re-narrating the Results' what?
- [ ] Conclusion answers the Introduction's question, without a
      section-by-section recap?
- [ ] Abstract and Conclusion are not word-swapped duplicates?
- [ ] No finding appears in more than three sections?

### 2.5 AI-trace and cleanliness

- [ ] Scanned against the AI-tone guardrails list?
- [ ] No inflation words (innovative, pioneering, groundbreaking) without
      evidence?
- [ ] Verb strength matches evidence strength?
- [ ] No internal scaffolding leaked into the prose?
- [ ] Zero bracketed placeholder tags of any kind, any variant?
- [ ] No process preambles ("Let me first analyze...", "Based on the
      evidence collected...", or any-language equivalents)?

### 2.6 Formulas and tables

- [ ] Display math in `$$...$$`, inline in `$...$`, no bare subscripts or
      HTML tags?
- [ ] Symbol explanations match the equation's symbols exactly?
- [ ] Structured data in tables, not run-on prose?
- [ ] Tables have caption, headers, units; captions not fused into body
      text?
- [ ] Draft-mode placeholder tables structurally complete (headers and
      units, `--` in value cells), not a vague "table pending"?
- [ ] No markdown bold, italics, or emoji inside paper prose?

### 2.7 Delivery format (full papers and multi-section drafts)

- [ ] Does the product need file delivery? (single paragraph or section:
      conversation output suffices, skip this block)
- [ ] If files: the draft written to the workspace as Markdown or LaTeX?
- [ ] If files: working ledgers (evidence map, tracing table) in separate
      files, not embedded in the prose?

## On failure

- A failed item: fix, then deliver.
- Unfixable without user materials: state the gap plainly in the delivery
  note; never dress the draft up as final.
- Final mode with any failure: do not call it submission-ready.
