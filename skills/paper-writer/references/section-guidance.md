# Per-section drafting guidance

## Table of contents

1. Abstract
2. Related Work
3. Methods
4. Results
5. Discussion
6. Conclusion
7. Future Work

Content contracts for drafting each non-Introduction section. The evidence
discipline applies throughout; this file adds the per-section form.
(Introductions have their own skill: intro-drafter.)

## 1. Abstract

Aim for 200-250 words. Structure: one sentence stating the problem and its
importance; one on the limitation of prior work; one on the key idea or
contribution; two to three on the method at a high level; two on the main
results (qualitative if no numbers were given); one on the significance.

No citations in the abstract. No undefined acronyms. Any name introduced
(framework, dataset, model) must appear again in the paper. No superlatives.
Write it last.

## 2. Related Work

Organize as themes, not as a chronological list. Two to four themes is
typical. For each theme: state the line of work, name two to four
representative works with a brief note on their approach, and end with one
sentence naming the specific limitation this paper addresses. That closing
sentence ties the theme to the paper's argument.

Do not write "for a survey, see ...". Do not write "while many works have
explored ...". Each paragraph should leave the reader knowing exactly where
this paper sits.

## 3. Methods

Open with a one-paragraph overview: name the framework (one sentence), state
the pipeline (one to two), list the modules (one per module). This paragraph
mirrors the Introduction's solution overview.

For each module, a subsection that states its purpose (which challenge it
addresses), describes the technical mechanism, optionally walks the paper's
running example through it, and notes practical implementation
considerations.

Equations: include only those the user supplied, or where a formal statement
is genuinely necessary for the mechanism. Never invent an equation. If a
formal expression is needed but the user has not provided one, write the
mechanism in prose, omit the equation, and raise it in the delivery note as
something for the author to supply. Format: `$$...$$` display, `$...$`
inline.

## 4. Results

Open with a brief paragraph naming the evaluation setup (datasets, baselines,
metrics), using only values the user actually gave.

For each result, follow describe-then-interpret: first state what was
observed (the number, the comparison, the trend), then what it means (which
challenge it resolves, which hypothesis it supports, which baseline it beats
and why).

Never invent numbers. If a result calls for a value the user did not give,
write qualitatively ("outperforms prior baselines by a substantial margin,
with exact values in the results table") and name the actual table from the
user's materials; if no such table exists yet, say so in the delivery note
rather than inventing a reference.

Keep observation and inference distinct: "we observe X" versus "this suggests
Y". Never collapse the two.

## 5. Discussion

The Discussion is where the paper earns its venue tier. Three layers:

1. **Mechanism**: explain why the results are what they are, connected to the
   paper's key idea. If the user gave a mechanism hypothesis, develop it; if
   not, propose the most plausible mechanism consistent with the observed
   results and label it plainly as a working hypothesis.
2. **Connection to prior work**: relate the findings to the limitations of
   prior work. Cite specifically where the user supplied references;
   otherwise search with the session's literature capability, and if nothing
   suitable is found, keep the connection at citation level or drop it. Never
   patch the gap with a placeholder.
3. **Limits and future work**: name two or three specific things this paper
   does not establish. Not a generic limitations paragraph: "we have not
   validated X under condition Y" or "the proposed mechanism for Z needs
   experiment W".

Voice discipline is critical here: "is consistent with" for agreement with
prior reports; "suggests that" for supported hypotheses; "we hypothesise
that" for proposed but untested mechanisms; "would require further
validation" for tentative claims. Avoid "proves", "demonstrates
conclusively", "establishes", unless the user stated the experiment provides
that level of proof.

## 6. Conclusion

One to two paragraphs. First: restate the problem and what the paper
contributed (one sentence each for key idea, methodology, main result).
Second: two or three concrete next steps or open questions.

Do not summarise section by section. Do not introduce new material. Do not
repeat the Abstract.

## 7. Future Work (when separate from the Conclusion)

Two to three items, each stating a specific gap this paper leaves open, why
it matters, and a feasible next experiment or extension. List the extensions
the findings most directly call for, not every possibility.
