# PhageAgent MCP 中文介绍

PhageAgent 现已支持 MCP，让 Codex、Claude Code 等 AI Agent 直接接入噬菌体研究服务。通过自然语言，你可以检索 PhageScope 数据库中约 260 万条噬菌体基因组、1.31 亿条蛋白质和 1260 万个蛋白家族，查询宿主、分类、生活方式与功能注释，获取基因组序列和已有蛋白结构链接，进行蛋白序列相似性搜索；也可以把分析需求交给 PhageAgent，持续查看进度、追问并获取结果。19 个工具覆盖从数据查询到分析任务的完整流程，无需在本地部署数据库。注册账号、创建个人令牌后即可接入。

- 平台与注册：https://phageagent.deepomics.org/register
- MCP 端点：https://phageagent.deepomics.org/mcp
- 开源接入文档：https://github.com/yiyabo/phageagent-mcp
- MCP Registry 标识：`io.github.yiyabo/phageagent`

数据规模为近似值，以服务实时返回为准。数据查询只读；分析任务由 PhageAgent 执行。每个令牌每天可读取 2 GB 数据，测试阶段分析任务暂无配额。
