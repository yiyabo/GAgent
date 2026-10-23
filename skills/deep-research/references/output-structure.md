# Report structure and formatting

## Table of contents

1. The survey skeleton
2. Title
3. Abstract
4. Introduction
5. Methodology section
6. Taxonomy section
7. Branch sections
8. Synthesis discussion
9. Open problems
10. Conclusion
11. References
12. Formatting discipline
13. Length

The deliverable reads like a real survey paper: explicit research questions,
a systematic method, a structured taxonomy, cross-branch synthesis, and
conclusions that the evidence has earned.

## 1. The survey skeleton

Numbering rule: all body sections are numbered consecutively (Introduction
through Conclusion); only the Abstract and References go unnumbered. Branch
count floats with the topic (typically two to four), so the section numbers
of the discussion, open-problems, and conclusion sections float too.

```
# [Topic]: [angle / core judgment]

## Abstract
[problem, scope, method, core findings, significance; one paragraph]

## 1. Introduction
[why this survey is needed; the research questions RQ1..RQn; how the
 report is organized]

## 2. Methodology
[search tools and perspectives; keywords; time range; inclusion and
 exclusion; final corpus size; why this taxonomy]
[non-CS topics add an evidence-type table: type, meaning, credibility]

## 3. Taxonomy
[the classification axes, branches, and empty cells (= gaps), shown
 before the detail]

## 4. [Branch one]
[thematic prose with in-sentence comparison; a comparison table when
 three or more works are comparable; caption states the conclusion]

## 5. [Branch two] ...

## N. Synthesis discussion
[cross-branch analysis: trends, tensions, shared findings]

## N+1. Open problems and future directions
[specific, literature-backed gaps]

## N+2. Conclusion
[answers RQ1..RQn, one by one; then the survey's own contribution]

## References
```

## 2. Title

Title = topic + angle, never topic alone.

- Weak: "Large language models in medicine"
- Strong: "Large language models in medical imaging: the gap between
  general capability and clinical usability"

## 3. Abstract

One paragraph, roughly 150-250 words. Problem (why this investigation),
scope (what it covers), method (how), core findings (the two or three that
matter), significance (why the reader should care). The abstract gives
direction; it does not enumerate every conclusion. Its job is to let the
reader decide whether to read on.

## 4. Introduction

Answers three questions:

1. **Why is this survey needed?** Not "the field matters" (that needs no
   survey), but "what existing understanding or existing surveys lack, and
   what this one adds".
2. **What exactly will it answer?** Two or three research questions, each
   concrete and answerable. "How is CRISPR doing clinically" is not an RQ;
   "which diseases have phase-III or approved CRISPR therapies" is.
3. **How is it organized?** One paragraph of structure preview.

## 5. Methodology section

Search perspectives (list them), main keyword groups, time range, inclusion
and exclusion criteria, final corpus size, and the reasoning behind the
taxonomy design. For non-CS topics, include the evidence-type table so the
reader can weigh different kinds of evidence.

## 6. Taxonomy section

The survey's skeleton, shown before the detail. A good taxonomy has MECE
axes, visible empty cells (each one a gap and a finding), identified
straddlers (works that challenge the axes), and branches that align with the
RQs so each branch answers part of the question set.

## 7. Branch sections

Internal structure of each branch:

1. One opening sentence placing the branch in the framework.
2. Thematic prose, comparing works within sentences: consensus, divergence,
   and the conditions that explain divergence.
3. A comparison table when three or more works are comparable (columns are
   dimensions; missing values read "not reported"; caption states the
   conclusion).
4. An optional one-line takeaway for long branches.

## 8. Synthesis discussion

Not a recap of the branches. Cross-branch analysis only: trends all branches
share; tensions between one branch's conclusions and another's premises;
methodological problems the core papers have in common (for example, none
independently replicated); the key cross-branch comparison table if none
appeared earlier.

## 9. Open problems

Specific and literature-backed, never "more research is needed":

- Weak: "this direction needs more study"
- Strong: "the [X by Y] cell of the taxonomy is empty: no work applies X
  under Y, although the branch-two evidence suggests it would matter
  because..."

Absence claims need evidence too: "no work retrieved" is only writable after
the search actually looked.

## 10. Conclusion

Answers the Introduction's research questions one by one; that closes the
loop the Introduction opened. No new evidence here. The final paragraph
states what the survey itself contributes: the new framing, the exposed
tension, or the mapped gap, not "we wrote a survey".

## 11. References

Strictly uniform format, numbered in order of first appearance:

```
[1] Author1, Author2, et al., "Title," Venue, Year.
[2] Author1, Author2, "Title," arXiv:XXXX.XXXXX, Year.
```

More than three authors: et al. Titles in quotes. No URLs, no DOIs. No
unverified entries.

## 12. Formatting discipline

1. Section headings are theme names, not mechanical labels.
2. No emoji.
3. Bold only for core judgments and first occurrences of key terms.
4. No boilerplate openings ("With the rapid development of AI..."); open
   with a concrete fact.
5. Citations as bracketed numbers.
6. Mixed-language text keeps technical terms in English with a half-width
   space between English and Chinese.
7. Evidence strength is carried by wording (the hedge ladder), never by
   explicit labels.

## 13. Length

No hard cap. Long enough to answer the RQs, and nothing that does not
advance them: the self-check is that deleting a paragraph must hurt an RQ's
answer, or the paragraph was padding.
