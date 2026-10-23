# Citation verification protocol

## Two-step verification

For every candidate reference:

**Step 1: existence.** Search the full title (or a distinctive fragment plus
the first author's surname).
- Found with matching title and authors: existence passes.
- Found but title or authors differ: flag and confirm whether it is the same
  work.
- Not found: grade UNVERIFIABLE; it does not enter the survey. Never keep it
  with a warning label attached.

**Step 2: quote accuracy.** Does the finding quoted in the text match the
paper's title and abstract? With metadata only, do not fabricate method
details or specific numbers.

## Five-grade verdict

| Grade | Meaning | Usable |
|---|---|---|
| VERIFIED | exists; quote matches | yes |
| MINOR | exists; small wording drift | yes, after correction |
| MAJOR | exists; quote misrepresents it | no, correct or delete |
| UNVERIFIABLE | existence cannot be confirmed | no |
| PAYWALL | exists; metadata insufficient for the claim | restricted to metadata-level statements |

## Grey zone means unused

An unconfirmable citation stays out. "There is probably a paper showing X"
with no retrieval hit: do not cite it. One fewer reference always beats one
invented reference.

In the report body, gaps left by unusable sources use the uniform
unconfirmed marker, for example "[unconfirmed: no work retrieved in this
direction]". This is a research report convention, not a paper convention:
survey reports are working materials addressed to the researcher, so an
explicit unconfirmed marker is honest signaling there. Paper prose (the
paper-writer and intro-drafter skills) forbids all such markers; the two
regimes are different products, and the difference is deliberate.

## Zero tolerance for invented citations

Detection signals:

- a title that matches the point being argued suspiciously perfectly;
- an author-title-year-venue combination with zero retrieval hits;
- one author appearing many times in the list but only once in retrieval.

On discovering an invented citation: delete it immediately, then re-examine
every judgment that depended on it.
