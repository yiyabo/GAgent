"""
LLM Prompts for node similarity detection and merge decisions.

These prompts are used by the TreeSimplifier to identify and merge similar nodes
in a plan tree, converting it into a DAG structure.
"""

# =============================================================================
# Batch Similarity Detection
# =============================================================================

BATCH_SIMILARITY_SYSTEM = """你是一个任务分析专家。你的任务是分析一组任务节点，找出语义相似的节点对。

相似性判断标准：
1. 任务名称相同或语义等价
2. 任务指令描述的是相同或高度相似的工作
3. 可以合并执行而不影响结果

注意：
- 只返回相似度 >= 0.8 的节点对
- 不要将父子关系的节点判定为可合并
- 考虑任务的上下文和依赖关系"""

BATCH_SIMILARITY_USER = """请分析以下任务节点，找出所有相似的节点对：

{nodes_text}

请返回JSON格式的相似节点对列表：
[
    {{"id1": <节点ID1>, "id2": <节点ID2>, "similarity": <0.0-1.0>, "reason": "<相似原因>"}},
    ...
]

如果没有相似节点对，返回空数组 []

只返回JSON，不要其他内容。"""


# =============================================================================
# Pairwise Merge Decision
# =============================================================================

MERGE_SIMILARITY_SYSTEM = """你是一个任务规划专家。你需要判断两个任务节点是否可以合并为一个。

合并条件：
1. 两个任务在语义上是相同或等价的
2. 合并后不会丢失重要信息
3. 合并不会影响任务执行的正确性

请谨慎判断，只有真正相似的任务才应该合并。"""

MERGE_SIMILARITY_USER = """请判断以下两个任务节点是否可以合并：

节点1:
- ID: {id1}
- 名称: {name1}
- 指令: {instruction1}

节点2:
- ID: {id2}
- 名称: {name2}
- 指令: {instruction2}

请返回JSON格式的判断结果：
{{
    "can_merge": true/false,
    "similarity": <0.0-1.0>,
    "reason": "<判断理由>",
    "merged_name": "<如果合并，建议的合并后名称>"
}}

只返回JSON，不要其他内容。"""
