<!-- Canonical copy. If this file is duplicated into another skill, edit THIS copy
     (paper-writer/references/) and re-sync; scripts/check_shared_sync.py enforces parity. -->

# Evidence discipline

## Table of contents

1. Legitimate sources and the evidence hierarchy
2. The Evidence Map
3. Provenance thinking (the write-time rule)
4. No placeholder tags: the only resolution path
5. Fabrication red-flag families
6. Experimental evidence gates
7. Contribution-to-evidence tracing
8. Real versus planned results, Draft versus Final mode

Before writing any prose that involves citations, experiments, or factual
judgment, get clear on what evidence you actually hold. This file is the
discipline that every drafting skill in this plugin follows.

## 1. Legitimate sources and the evidence hierarchy

Every factual claim in generated prose has exactly three legitimate origins:
something the user provided, something retrieved and verified during the
session, or uncontroversial field common knowledge. **Model memory is never a
source.**

| Level | Source | Can support | Cannot support |
|---|---|---|---|
| **L1 full text / data** | The user supplied the full paper, data, figures, or logs | any claim | none |
| **L2 abstract** | The user supplied an abstract, or retrieval returned one | research direction, headline findings | specific numbers, method steps, implementation detail |
| **L3 metadata** | Retrieval returned title, authors, year, venue | "X et al. (Year) addressed Y" | method detail, performance numbers, limitation analysis |
| **L0 field common knowledge** | Facts a practitioner accepts without citation, provided (a) removing the sentence does not weaken the argument and (b) it contains no specific numbers, names, or quantified comparisons | background framing statements | any claim with numbers, names, or comparisons |
| **L4 model memory** | Anything you merely "remember knowing" | **nothing** | **everything** |

The L0 test: if you have to hesitate about whether a statement is common
knowledge, it is not. Search for a source instead.

## 2. The Evidence Map

For a full-paper task, produce an Evidence Map and keep it as a working file.
For a single section or paragraph, run the same inventory mentally, but any
paragraph that will carry citations must have its sources confirmed before
drafting.

```markdown
| ID | Source | Level | Supports | Cannot support | Planned use | Risk |
|---|---|---|---|---|---|---|
| E1 | user: results table, results/main.csv:12 | L1 | method detail, exact numbers | none | Results, Discussion | none |
| E2 | retrieval: Author (2023), metadata | L3 | "addressed X using Y" | method steps, numbers | Related Work | metadata-only |
| E3 | user: abstract fragment | L2 | direction of findings | specific numbers | Introduction | no numbers |
```

Filling rules:

- One row per source, level stated.
- **The "cannot support" column is mandatory**; its job is to keep the
  source's boundary in view while writing.
- L3 rows carry the `metadata-only` risk marker.
- L4 never enters the map. It is not evidence.
- When the source is a file in the user's workspace, record the path (and line
  or table when useful) so every claim is traceable to an inspectable
  artifact.

## 3. Provenance thinking (the write-time rule)

This is the core anti-fabrication mechanism, and it runs **before** the words
are generated, not after. Before drafting each paragraph, settle internally
what the paragraph will claim and which source (user input, retrieval result,
or L0 common knowledge) backs each claim. A claim with no source is excluded
at planning time, not written down and tagged later.

Write clean prose directly. Do not embed any provenance markers, editorial
brackets, or to-be-confirmed annotations in the generated text. Same-context
self-review cannot reliably catch fabrication after the fact; the defense is
this pre-writing check, plus the independent verification pass for citations.

## 4. No placeholder tags: the only resolution path

Delivered prose never contains bracketed placeholders of any kind: no
"citation needed", no "to be verified", no "to be specified by authors", no
"to be confirmed", and no variants. A placeholder is not a safety valve; it is
an unfinished job.

When a claim lacks a source, the resolution path is fixed and has no cheap
exit:

1. Search for a real source (use the session's literature-search capability).
2. Found: weave in the real citation.
3. Not found after two or three keyword variants: rewrite the sentence so it
   no longer needs the claim, or delete the sentence.

If something genuinely needs the author's confirmation, say so in two or
three plain-language lines after the prose, never inside it.

## 5. Fabrication red-flag families

The details most tempting to invent, and therefore banned unless the user
supplied them, fall into five families:

| Family | Examples | Rule |
|---|---|---|
| Application scenarios | "photovoltaic farms", "autonomous driving" | only scenarios the user named |
| Mechanisms and causal stories | "works because of spectral aliasing" | only mechanisms the user described |
| Magnitudes and scale words | "hundreds of categories", "tenfold difference", "fewer than ten pixels" | only magnitudes the user gave |
| Procedural and implementation detail | scale item counts, consent procedure, software versions, beam size, dates, missing-data rates | only what the user stated; otherwise omit |
| Identifiers | case citations, statute numbers, standard numbers, arXiv IDs | only identifiers the user provided or retrieval confirmed |

The general form: **a concrete detail the user did not provide is not written,
period.** Omission is always safer than plausible invention.

## 6. Experimental evidence gates

When prose involves experimental results, Discussion, or contribution claims,
check each gate:

| Gate | Check | On failure |
|---|---|---|
| D0 data boundary | data source, sample, splits explicit? | describe the experimental plan, not results |
| D1 metric definition | metric and computation defined? | explain the metric's purpose, no effectiveness verdict |
| D2 comparisons | baselines and ablations identified? | downgrade the contribution to design motivation |
| D3 results provenance | numbers from user-confirmed real runs? | Draft mode: hedged phrasing ("preliminary results suggest"); Final mode: no effectiveness claims |
| D4 contamination | planning numbers separated from real numbers? | keep planning data in tables marked as planning data, never in prose as results |
| D5 conclusion boundary | conclusions limited to the tested conditions? | delete or bound the generalization |

## 7. Contribution-to-evidence tracing

Before delivering a full paper, trace every contribution claim:

```markdown
| Contribution | Method section | Experiment / figure | Evidence status |
|---|---|---|---|
| C1: propose framework X | 3.1 | Table 2 ablation | L1 confirmed |
| C2: mechanism Y | 3.2 | none | unsupported: downgrade |
```

A contribution with no experimental trace is, in Draft mode, recorded as
pending validation in the ledger (not annotated in the prose); in Final mode
it is downgraded or moved to Future Work.

## 8. Real versus planned results, Draft versus Final mode

- The user says the results are real and complete: write "we observe",
  "results show".
- The user says the results are expected, planned, or uncertain: write "the
  authors report", "preliminary results suggest", and never present them as
  findings. No bracketed annotations.
- **Draft mode** (default): structural placeholders are allowed in tables
  (standard table skeletons with `--` in value cells), but factual claims in
  prose follow every rule above.
- **Final mode** (the user says submission-ready or camera-ready): no pending
  items of any kind; anything unresolved blocks the "ready" claim.
