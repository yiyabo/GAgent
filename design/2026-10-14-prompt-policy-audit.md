# Prompt 链路审计与修复

基线：`1341a28f25a8f60f44cd2ce83545d9f72b867021`。2026-10-14 检查时 Mac 和 .8 源码一致，实际 app 镜像为 `279518a87c92`，118 个库的部署门禁为 idle。审计不触发真实模型或生物信息服务，不重跑、删除或改写已有用户任务。

## 已核对的组装入口

| 路径 | 入口与内容 |
| --- | --- |
| 普通 / 结构化聊天 | `routers/chat/prompt_builder.py`，`prompts/en_US.py` 的 action catalog / common / bound / unbound rules |
| native / strict DeepThink | `services/deep_think/prompts.py` 的共享策略、Plan requirement、工具指引、task context、reference context、后续轮提示 |
| 原生与注册工具描述 | `tool_box/native_tool_schemas.py`、`tools_impl/plan_tools.py`；schema 参数与 handler 分离 |
| 计划生成 | `plans/plan_generation.py` 的材料判断，`plans/plan_decomposer.py` 的 root→composite→atomic、依赖及输出声明 |
| 计划执行 / 外部委托 | `plans/executor_prompts.py`、`executor_deepthink.py`、`executor_delegate.py`、`task_delegate_executor.py` |
| 验收补交 / 收尾 | `executor_resume_repair.py`、DeepThink contract repair query、`audit_repair_loop.py`、`deep_think/synthesis.py` |
| 记忆 / Skills | 实际生效的 `memory/context_recall.py`、`skill_learning/context.py`、`skills/skills_loader.py` 及仓库 Skills；不能只检查兼容 `format_memories` |
| 独立事后分析 | `conversation_quality/prompt.py` 仅评价完成对话，不负责执行，不将其规则当成聊天指令 |

图谱 MCP 在本会话没有可调用工具，采用范围限定的源码 / 字符串检索。未把文件里引用的指令当成本轮用户授权。

## 发现的具体问题

| 问题 | 基线行为 / 风险 | 修复 |
| --- | --- | --- |
| 旧模板仍自动建 Plan | en_US 的 4 个规则仍允许多步骤代码 / 数据处理自动建 Plan，bound rule 允许复杂工作时先拆任务；与上一轮新规则冲突 | 统一明确请求才创建 / 修改持久树，普通多步骤用内部 todo |
| 建计划后继续审核 | native schema 引导 `create` 后 `prefer review`，注册描述列出自动 review / optimize 的流程 | 返回实际树及未完成拆解状态，只有用户同时要求才继续其他操作 |
| review 被视为 mutation | requirement / retry nudge 把审核称为必须修改；synthesis 把所有未完成 Plan 操作描述成创建失败 | review 只读；按真实操作回执说明失败，不授权执行 / 修复 / 改树 |
| 后台规则混杂 | native 通用规则按长短决定 background；结构化模板把 Plan 执行描述成直接 code_executor | 只有明确请求执行已有 Plan 任务才交后台，立即回 job 回执；普通工具继续聊天，远程异步工具保留自身回执语义 |
| 写作不落文件 | Executor 把设计、写作、分析都当 text-only；仅列旧 acceptance，不完整展示 required_outputs | 以 OutputSpec 决定文本或文件交付；完整目标、格式、数量、内容约束进入执行提示 |
| 文件 / 工具规则过度断言 | 微调禁止报告原图缺失且强制 3 格式；会话隔离禁止所有外部路径；压缩提示声称 pandas 都不能读；工具名被偶然 / 否定提及也强制使用 | 据实际证据报告缺失，保留请求格式；允许精确附件 / canonical 依赖引用；按格式选读法；明确方法与偶然提及分开 |
| 旧记忆 / 方法可能扩大任务 | 上轮只修兼容 format_memories，实际 recall / learned skill 注入仍没有明确授权边界 | 在实际 formatter 和共享策略中声明只提供背景 / 方法，不复活旧工作流 |
| 最新要求与路径丢失 | 聊天硬截取前 100k 字符可能丢掉末尾 user request；执行提示硬截断契约；长 live turn 在截取历史后不能去重；依赖摘要 regex 只保留扩展名 | 先删可选 reference block，保护本轮要求 / 规则；不可放下明确失败；裁剪前去重；保留完整证据文件路径和稳定顺序 |
| 补交再次走重流程 | strict 要求所有文章 / 报告都用整套 manuscript_writer；固定至少 3 次恢复；repair 提示禁止任何绝对目标 | 小修 / 补交可直接写文件；按具体错误纠正，不强制无意义次数；遵循当前 exact target，不拷贝到无关备份 |

## 实现约束

`app/services/prompt_policy.py` 只包含共享文本常量，没有新路由、状态机或权限裁判。保留原来的两种 intent、工具 dispatch 硬检查、计划执行器、验收器、后台 job 与调用预算。native / strict / structured 从同一产品规则出发，协议格式仍不同。生成阶段允许构建完整层次，执行阶段保持树与契约不变。

非目标：不放宽科学内容验收，不把存在文件当成科学正确；不修改用户历史 task tree，不自动重跑研究；不更换模型、提高预算或增加真实模型评测额度。已有 accepted / partial / published 的含义保持不变。

## 验证与发布

新增组装级用例检查未绑定 / 已绑定聊天、只读审核、重试、真实 recall / learned skill formatter、原生与注册工具描述、超长历史 / 本轮请求、精确多文件输出契约、绝对目标补交，以及依赖摘要路径保存。原有路由、native / strict、Plan 执行、补交、SSE golden 和研究旅程集成继续回归，模型及外部科学服务使用 mock。

测试通过证明组装规则和确定性行为，不证明实际模型永不犯错或完成率已经提升。最终测试数量、CI、代码 SHA、实际镜像与部署 / 回滚记录写入 gitignored 的原 `HANDOFF.md` / `LOCAL_INFRA.md` 并同步三机。

无数据库迁移；回滚使用发布前应用镜像并对齐源码，保留后续用户数据库和历史文件。前端源码不变，不需要新 gateway 镜像。正常发布仍需 CI 全绿、空闲门禁和新一致性备份。
