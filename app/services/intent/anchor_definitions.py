"""Anchor sentences for each intent type.

Each intent has 6-8 representative sentences (Chinese + English) that capture
the **semantic center** of that intent.  Edge cases are handled by the
deterministic rule engine in ``request_routing.py``; anchors only need to
represent the typical, unambiguous core.

The anchor vectors are averaged into a single centroid per intent at startup.

NOTE: Phase 2 collapsed IntentType to {chat, execute_task}. The semantic
intent classifier is no longer called from the routing pipeline, but these
anchors are kept for the classifier's unit tests and potential future use.
"""

from __future__ import annotations

from typing import Dict, List

# Keys must match ``IntentType`` literals in request_routing.py.
INTENT_ANCHORS: Dict[str, List[str]] = {
    "execute_task": [
        "Please implement this feature for me",
        "帮我把这个功能实现一下",
        "Run the pipeline and submit the results",
        "跑一下流程然后提交结果",
        "Write the code for this module",
        "把这个模块的代码写出来",
        "Fix this bug and deploy the changes",
        "修复这个问题并部署",
        "Search for the latest papers on CRISPR gene editing",
        "帮我搜索最新的CRISPR基因编辑文献",
        "Unzip this archive and extract the files",
        "把这个压缩包解压出来",
    ],
    "chat": [
        "What do you think about this approach",
        "你觉得这个方案怎么样",
        "Can you explain how this works",
        "你能解释一下这个是怎么工作的吗",
        "Tell me more about this concept",
        "给我详细讲讲这个概念",
        "What is the difference between A and B",
        "A和B之间有什么区别",
        "Show me the contents of this file",
        "给我看看这个文件的内容",
    ],
}
