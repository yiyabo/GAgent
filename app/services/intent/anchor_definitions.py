"""Anchor sentences for each intent type.

Each intent has 6-8 representative sentences (Chinese + English) that capture
the **semantic center** of that intent.  Edge cases are handled by the
deterministic rule engine in ``request_routing.py``; anchors only need to
represent the typical, unambiguous core.

The anchor vectors are averaged into a single centroid per intent at startup.
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
    ],
    "research": [
        "Search for the latest papers on CRISPR gene editing",
        "帮我搜索最新的CRISPR基因编辑文献",
        "Find recent publications about phage therapy",
        "查找关于噬菌体治疗的最新研究进展",
        "What does the current literature say about this topic",
        "目前关于这个方向的文献综述是什么样的",
        "Look up the state of the art benchmarks",
        "查一下最先进的基准测试结果",
    ],
    "local_read": [
        "Show me the contents of this file",
        "给我看看这个文件的内容",
        "Open and read this document",
        "打开这个文档读一下",
        "What is in this file",
        "这个文件里面是什么",
    ],
    "local_inspect": [
        "Analyze the data structure in this file",
        "分析一下这个文件里的数据结构",
        "What columns does this dataset have",
        "这个数据集有哪些字段",
        "Break down the schema of this table",
        "拆解一下这个表的结构",
        "List all the data fields and their types",
        "列出所有的数据字段和类型",
    ],
    "local_mutation": [
        "Unzip this archive and extract the files",
        "把这个压缩包解压出来",
        "Rename this file and move it to another folder",
        "重命名这个文件然后移到另一个目录",
        "Delete these temporary files",
        "把这些临时文件删掉",
        "Copy the results to the output directory",
        "把结果复制到输出目录",
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
    ],
}
