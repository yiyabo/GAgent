"""
LLM Prompts for node similarity detection and merge decisions.

These prompts are used by the TreeSimplifier to identify and merge similar nodes
in a plan tree, converting it into a DAG structure.
"""

# =============================================================================
# Batch Similarity Detection
# =============================================================================

BATCH_SIMILARITY_SYSTEM = """You are a task-analysis expert. Analyze a set of task nodes and identify semantically similar node pairs.

Similarity criteria:
1. Task names are identical or semantically equivalent.
2. Task instructions describe the same or highly similar work.
3. The two tasks can be merged without affecting correctness.

Important constraints:
- Return only pairs with similarity >= 0.8.
- Do not mark parent-child nodes as mergeable.
- Consider task context and dependency relationships."""

BATCH_SIMILARITY_USER = """Analyze the following task nodes and find all similar pairs:

{nodes_text}

Return a JSON array of similar node pairs:
[
    {{"id1": <NODE_ID_1>, "id2": <NODE_ID_2>, "similarity": <0.0-1.0>, "reason": "<WHY_SIMILAR>"}},
    ...
]

If no similar pairs exist, return [].

Return JSON only. Do not add extra text."""


# =============================================================================
# Pairwise Merge Decision
# =============================================================================

MERGE_SIMILARITY_SYSTEM = """You are a task-planning expert. Decide whether two task nodes should be merged into one.

Merge conditions:
1. The two tasks are semantically identical or equivalent.
2. No important information is lost after merging.
3. Merging will not affect execution correctness.

Be conservative. Only truly similar tasks should be merged."""

MERGE_SIMILARITY_USER = """Decide whether the following two task nodes can be merged:

Node 1:
- ID: {id1}
- Name: {name1}
- Instruction: {instruction1}

Node 2:
- ID: {id2}
- Name: {name2}
- Instruction: {instruction2}

Return your decision in JSON:
{{
    "can_merge": true/false,
    "similarity": <0.0-1.0>,
    "reason": "<RATIONALE>",
    "merged_name": "<SUGGESTED_MERGED_NAME_IF_APPLICABLE>"
}}

Return JSON only. Do not add extra text."""
