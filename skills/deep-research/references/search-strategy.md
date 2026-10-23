# Search strategy

## Keyword construction

Keywords are constructed systematically, not free-associated. Patterns:

**Pattern 1: core method x application domain**
- "retrieval augmented generation" + "question answering"
- "CRISPR base editing" + "clinical trial"

**Pattern 2: core mechanism x target task**
- "chain of thought" + "scientific reasoning"

**Pattern 3: field + survey / benchmark / meta-analysis**
- "LLM agent survey 2025 2026"
- "social media adolescent mental health meta-analysis"

**Pattern 4: key researcher x direction** (when the brief names specific
researchers)
- "Haidt social media adolescent"
- "Orben screen time well-being"

**Pattern 5: citation network of known papers**
- extract key terms from already-found core papers; search what they cite
  and what cites them

Try at least one round per pattern. In multi-perspective search, each
perspective uses one or two patterns; three to five perspectives at about
two rounds each sets the floor, with no ceiling: coverage decides.

## Blind-spot discovery

After the second round, run a blind-spot check:

1. **Coverage**: does every sub-direction of the brief have three or more
   works?
2. **School check**: is any method school entirely absent?
3. **Citation-chain check**: are the high-frequency works cited by the found
   papers themselves in the list?
4. **Discard check**: do the "found but not planned for use" results point
   to an uncovered sub-direction? Do not throw them away; they are the
   blind-spot clues.
5. **Recency check**: anything important from the last year? All-old results
   usually mean the keywords missed the current wave.

## Adjusting by discipline

Literature distributes differently across fields:

- **CS / AI**: arXiv preprints dominate and move fast; the last six months
  can be decisive.
- **Biomedicine**: clinical journals matter; search "clinical trial" plus
  the disease name; note trial phase.
- **Social science**: search effect sizes and designs (RCT, DID, IV)
  alongside the topic.
- **Cross-disciplinary topics**: search each discipline separately, then
  merge; a single discipline's keywords will not surface the others.
