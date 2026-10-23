<!-- Canonical copy. If this file is duplicated into another skill, edit THIS copy
     (paper-writer/references/) and re-sync; scripts/check_shared_sync.py enforces parity. -->

# Citation verification ladder

## Table of contents

1. Why verification must be independent
2. The capability ladder
3. When to trigger the independent pass
4. What the verifier does with each citation
5. Status grades and resolution routes
6. Honest boundary
7. Convergence rule

This file governs one hallucination class only: whether the citations in
generated prose are real and honestly quoted. It does not replace the other
delivery checks.

## 1. Why verification must be independent

Most self-review happens in the same context that produced the text. For
citation hallucination that is nearly worthless: the model that wrote "Smith
et al. (2021) proposed X" from memory will, asked to re-check, consult the
same memory and confirm it. Generation and review are one cognitive act
sharing one error.

Citation hallucination (invented references, swapped authors or years, wrong
identifiers, metadata quoted as if full text) is also the most mechanically
falsifiable hallucination class: a reference either turns up in retrieval or
it does not; metadata either matches or it does not. That is exactly the part
worth outsourcing to an independent step, so the defense rests on mechanical
retrieval instead of on the model being honest with itself.

## 2. The capability ladder

**The discipline never degrades; only the verification mechanism degrades, and
any degradation is disclosed to the user in the delivery note.**

- **Rung 1, fresh-context verifier (preferred).** If the environment can spawn
  a sub-agent with a clean context, dispatch one. Its input is only the prose
  (with citation marks) and the reference list. It does not receive the
  drafting reasoning, so the writer's confidence cannot contaminate the check.
  It verifies each entry with the session's literature-search capability.
- **Rung 2, same-context grounding.** No sub-agents, but retrieval is
  available (a scholarly search tool, web search, or shell access to public
  APIs such as Crossref, Semantic Scholar, arXiv, DBLP). Re-derive every
  reference entry from actual retrieval responses, never from memory, and
  check each claim against what retrieval returned. Weaker than rung 1; say
  so.
- **Rung 3, closed book.** No retrieval at all. Then: cite only references the
  user supplied; confirm each is quoted at citation level only; add no other
  references; and state in the delivery note that independent verification was
  not performed.

For literature without good API coverage (much Chinese-venue literature, for
example), web retrieval counts one level weaker than indexed metadata; treat
its results as L3 at best and say so when it matters.

## 3. When to trigger the independent pass

Four mutually exclusive tiers, no middle ground:

| Situation | Independent pass |
|---|---|
| Full paper or multi-section draft, or Final mode | **mandatory**, regardless of citation count |
| Otherwise, cumulative citations of 3 or more | **mandatory** |
| Otherwise, single paragraph or short section with 2 or fewer citations | may be skipped; the inline delivery checklist covers it |
| No citations at all | not triggered |

The threshold sits at 3 because Introductions and Related Work sections pass
it almost immediately; only genuinely light fragments fall to the inline
side. If any statement here conflicts with a skill's own text, this table
wins.

## 4. What the verifier does with each citation

For every entry in the reference list:

**Step 1: existence and metadata.** Search with at least three query forms:
full title; author plus keywords plus year; core keywords alone. Compare
title, authors, year, venue against what retrieval returns.

Distinguish **"not found" from "could not search"**. Only a normal retrieval
response with zero hits across all three query forms counts as not found. Tool
errors, timeouts, rate limits, or a systematic pattern of empty responses mean
the search itself failed: grade INCONCLUSIVE, never NOT_FOUND. One
infrastructure hiccup must never delete a real reference.

**Step 2: quote strength, back at the citation site.** Prose saying "Author et
al. addressed Y" is citation-level: matching metadata suffices. Prose saying
"achieved 92 percent on benchmark B" or describing their pipeline is
full-text-level: metadata cannot support it; grade OVERCLAIM unless the user
supplied the full text or abstract that does.

## 5. Status grades and resolution routes

| Status | Meaning | Resolution |
|---|---|---|
| **VERIFIED** | found; metadata matches; quoted at citation level | keep |
| **METADATA_MISMATCH** | the work exists but author, year, or venue differ | correct the entry from retrieval; if the retrieval backend itself contradicts multiple sources or plain sense (a classic paper dated in the future), do not trust it blindly: suspend the entry for the user to decide |
| **NOT_FOUND** | normal retrieval, zero hits across 3 or more query forms | delete the citation and the sentence it props up, or find a real replacement through retrieval; never keep it as fact |
| **OVERCLAIM** | the work is real but the prose claims more than the available level supports | rewrite the sentence down to citation level ("X et al. addressed Y") |
| **INCONCLUSIVE** | tool error, timeout, rate limit, systematic empty responses | neither pass nor delete; retry, and if it persists, suspend the entry and hand it to the user in the delivery note |

Suspended entries are listed in the plain-language delivery note. No status
ever resolves by inserting a bracketed tag into the prose.

## 6. Honest boundary

This pass catches: invented references (NOT_FOUND is its core value), swapped
metadata, and metadata quoted as full text (OVERCLAIM).

It cannot catch: a real, citation-level reference whose underlying claim is
itself wrong (that needs the full text, which belongs to the evidence-matching
check, not here); and non-citation fabrication such as invented institutional
details or numbers (that is prevented at write time by the evidence
discipline, not repaired here).

Metadata correction and OVERCLAIM judgments still involve model judgment, and
the retrieval backend itself can be wrong; the METADATA_MISMATCH safeguard
exists for exactly that case. This pass hardens the citation layer; it is not
a blanket guarantee.

## 7. Convergence rule

Delivery may proceed only when **no unresolved problem entries remain**. That
is not the same as "everything VERIFIED": an entry is resolved when it is
VERIFIED, corrected, deleted, rewritten to citation level, or explicitly
suspended to the user. Unresolved NOT_FOUND, METADATA_MISMATCH, or OVERCLAIM
entries route back to the writer for targeted fixes (only the affected
sentences, not a rewrite), then re-verify.
