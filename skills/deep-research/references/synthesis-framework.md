# Synthesis framework

## Table of contents

1. The three-step flow (extraction, clustering, serial synthesis)
2. Weaving: in-sentence cross-comparison
3. Comparison-table design
4. Presenting contradictions
5. The per-section skeleton

## The three-step flow

### Step 1: extraction

From each paper, extract four things that bear on the research brief:

- **Core finding**: one or two sentences.
- **Method signature**: what method, and how it differs from the others.
- **Conditions**: under what conditions the finding holds; known limits.
- **Relations**: whom it supports, challenges, or extends.

Irrelevant papers are dropped whole at this step.

### Step 2: thematic clustering and MECE taxonomy

Cluster by **research question or method school**, never by paper. Mark each
outline node with:

- **Presentation**: paragraph (dense synthesis of several works) or table
  (side-by-side comparison of three or more comparable works).
- **Assigned works**: which papers this node may cite.

Taxonomy rules:

- Axes mutually exclusive, collectively exhaustive.
- An empty cell is a gap, and a gap is a finding ("no work applies X to Y"
  is a result worth reporting).
- Papers that straddle categories get discussed explicitly; they challenge
  the axis and are often the most insightful.
- If one axis produces too many straddlers, change the axis.

### Step 3: serial section synthesis

Write section by section in outline order. Each section's input is the
brief, the works assigned to that section, and the sections already
written. Serial, not parallel: parallel drafting produces duplication,
style drift, and missing transitions.

Constraint inherited from step 2: each section cites only its assigned
works. Writing from "whatever I remember of the pool" is how citations
drift.

## Weaving: in-sentence cross-comparison

This is the measurable difference between a survey and a search-result
summary. Human-written surveys compare several works inside one sentence;
naive generation summarizes papers one at a time in isolation.

**Anti-pattern (serial listing)**:

> Smith et al. (2024) proposed method A for task X and achieved 85 percent
> accuracy. Lee et al. (2025) proposed method B for task X and achieved 87
> percent.

**Pattern (in-sentence comparison)**:

> While Smith et al. [1] and Lee et al. [2] both target task X, they take
> fundamentally different routes: Smith et al. lean on retrieval
> augmentation, reaching 85 percent, whereas Lee et al. rely on in-context
> learning, reaching 87 percent at three times the inference cost. Wang et
> al. [3] further show the retrieval-based strategy generalizes better to
> the related task Y, suggesting broader applicability despite the lower
> headline number.

**Operating rule**: every thematic paragraph contains at least one sentence
that cites and compares two or more works. A paragraph citing a single work
reads as an abstract, not a survey.

## Comparison-table design

- Columns are comparison dimensions (method trait, strength, limitation,
  metric), not paper attributes.
- Rows are the works.
- **The caption states the conclusion** ("retrieval methods lead on recall
  but cost 3-5x the latency of end-to-end methods").
- Missing information reads "not reported"; cells are never left blank and
  never filled with invented numbers.
- Drop sparse columns (a dimension only one or two works report does not
  deserve a column).

## Presenting contradictions

Contradictions between works are among the most valuable content a survey
has. **Do not average them away.**

When A finds X effective and B finds X ineffective:

1. Present both findings plainly.
2. Analyze the condition differences (dataset, metric, setting, sample
   size).
3. If a judgment is possible, give the lean ("under condition Z, B's
   evidence is stronger").
4. If not, say so: "current evidence does not decide this; a test under W
   would".

Never: write "this is debated" and move on; average the two; cite only the
side that fits the narrative.

## The per-section skeleton (run before writing every section)

1. **Claim**: what do these works, taken together, tell us? Not "what does
   this section cover".
2. **Strongest supporting evidence**: which works, and does independent
   convergence exist?
3. **Counter-evidence or qualifications**: which works contradict or bound
   the claim?
4. **Condition differences**: what explains the disagreement?
5. **Cross-section links**: what does this section support or challenge
   elsewhere?

The skeleton is never printed; it drives the prose. Self-check afterwards:
delete the citations mentally; if what remains is still an argued judgment
rather than a list of facts, the section is sound.
