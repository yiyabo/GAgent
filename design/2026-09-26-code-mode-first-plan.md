# code-mode 优先：把单脚本留在进程内，把 pi 降级为"按需升级"

日期：2026-09-26
状态：规划（P0 部分待实施）
上游依据：`design/2026-09-24-code-mode.md`（code-mode 权威设计，直接抄 Hermes 的两条契约）
触发证据：2026-09-26 的 28 任务双臂 A/B（`sample28_split` / `sample28_pi`）

---

## 0. 先把"能不能抛弃 pi"写成可验的命题

pi（外部 Claude-Code 风格编码 harness）现在唯一不可替代的地方，是三类任务：

1. **需要装包/换环境**；
2. **单次超过 5 分钟的长流程**；
3. **多文件实现 + 反复调试**。

**判定标准**：当 code-mode 能独立完成这三类任务、且成功率不低于 pi 时，才把
`code_executor` 从默认工具面降级为"可选升级路径"。

**现在的评测测不到这个判定**：`sample28_*` 的 28 个任务**全是单脚本**
（读一个文件、算一次统计、画一张图）。所以 §4 的补任务是本规划的第一步，
不是可选项。

已经得到的事实（同一任务、同一检查，已核实工具归属）：

| 任务 | `execute_code`（进程内） | `code_executor`（委派） |
|---|---|---|
| t01 读 CSV | **25.6s** | 111.9s（本地车道） |
| t07 读 xlsx | **29.8s** | 720s 崩 |
| t09 分组汇总 | **54.5s** | 817s（本地车道） |
| t13 透视表 | **78.6s** | 154.6s（pi 车道） |
| t10 过滤排序 | — | 89.6s（pi 车道）/ 900s 超时（本地） |

差 4-15 倍，量级差异，不是优化空间。**顺带结论：本地 codegen 车道
（`auto+split` 的分析路）是三者最差的，建议退役**——它的存在只是多一个坏选项。

---

## 1. 现状核查：code-mode 已经比我以为的更完整

| 能力 | 事实 | 位置 |
|---|---|---|
| 执行形态 | **独立子进程**（`sys.executable`、`start_new_session`），cell 崩只杀内核 | `kernel.py:458-499`, `487-496` |
| 环境隔离 | 先按名剔密钥（`KEY/TOKEN/SECRET/PASSWORD/...`），再只留白名单前缀；注入 RPC token | `env_scrub.py:18-29`, `31-47`, `89-102` |
| 第三方库 | 用的是**应用自己的 venv** → 有 openpyxl/pyarrow/sklearn/mplfonts（正是执行器镜像缺的那批） | `kernel.py:488`, `env_scrub.py:97-102` |
| 工具调用 | 7 个只读工具白名单（`web_search, literature_pipeline, document_reader, vision_reader, lightrag_query, sequence_fetch, url_fetch`），服务端强制 | `config.py:27-35`, `rpc.py:151-160` |
| 内核生命周期 | 每 (session, allowlist, cwd) 一个，跨轮复用；LRU 上限 4 + 空闲 1800s 回收 | `kernel.py:279`, `660`, `292-320` |
| 硬限制 | cell 墙钟 300s、每 cell 工具调用 50 次、单次工具调用 300s、stdout 50KB head/tail + 全文 spill | `config.py:37-41`, `output.py:29-64` |
| 失败提示 | 四类正则 → 一句可执行建议 | `output.py:123-166` |
| 测试/文档 | 10 个测试文件 + 权威设计文档 | `app/tests/tools/test_execute_code_*.py`, `design/2026-09-24-code-mode.md` |

---

## 2. 五个缺口（每个都能指到代码）与最小改法

### G1 产物不落地：cell 写的文件进不了 deliverables ← **最影响实用性**

- 事实：`execute_code` 的结果 payload **不含任何产物路径**（只有 stdout 与 spill 文件）；
  `code_executor` 那边有整套提升链路（`_promote_results_to_unified_dir` /
  `_promote_task_results_to_session_root` / `_promote_project_level_strays`），
  code-mode 一个都没有；cell 自己也**无权发布**（白名单里没有任何写工具）。
- 后果：code-mode 画的图/写的 CSV 只能靠模型**另起一次工具调用**交给
  `deliverable_submit`；忘了这一步，产物就"消失在磁盘上"。
- 最小改法：① cell 结束后按"时间窗 + 位于会话 work_dir 下"识别新文件，结果里加
  `produced_files`；② 复用 `code_executor_promotion` 的提升函数把图片/文档送进
  unified dir；③ 让既有的内联图后处理（`_ensure_inline_images`）认这些路径。
- 验收：cell 内 `plt.savefig("results/x.png")` → 最终答案**内联**该图 + Artifacts 面板可见。

### G2 没有装包通道 ← 三类护城河之一

- 事实：缺包时工具自己的提示是"**去用 code_executor**"（`output.py:136-141`），
  即设计上把依赖问题判给委派；容器又**直连 pypi.org 不通**
  （`deploy/Dockerfile.runtime-overlay:31`）。
- 附带不一致：`inject_env_mutation_guard`（`PIP_REQUIRE_VIRTUALENV` +
  conda/npm 包装脚本）只接在 `code_executor.py:745` / `:858` /
  `local_interpreter.py:77`，**code-mode 内核从来没接**——所以 cell 现在能直接调用
  真的 `pip`/`conda`/`npm`（`env_scrub.py:31-47` 保留了 `PATH`）。
- 最小改法：① 建内网 wheel 缓存目录，允许 `pip install --no-index --find-links=<dir>`；
  ② 把同一套 env-mutation guard 也接进 `build_child_env`（安全收益，顺带消除不一致）；
  ③ 提示文案改成"先找等价库/本地 wheel，再考虑升级"。
- 验收：一个 `pip install` 类任务在 code-mode 内完成，且不能动 host 的 conda/npm。

### G3 长任务 = 直接丢状态 ← 三类护城河之二

- 事实：超时/中断 → 杀整个进程组 → globals 全丢，**没有任何快照/恢复代码**；
  下一次调用只是新起内核（`kernel.py:543-546`, `243-276`）。
- 最小改法：① 超时前 best-effort 快照可 pickle 的全局到会话目录，下次自动恢复；
  ② 支持"后台 cell"（长任务变成可轮询的 job，主循环用一次 `execute_code` 查结果）——
  比单纯抬高 300s 上限安全得多。
- 验收：一个 6 分钟的数据任务不丢上下文、可续跑。

### G4 结果可读性有三处问题（两处疑似真缺陷）

- (a) **native 车道**：结果 JSON 走 12k 字符 cap，但 `_compact_tool_result_for_llm`
  **没有 execute_code 分支**（`dispatch.py:616-625`）→ 50KB stdout 可能直灌上下文。
- (b) **chat 动作车道**：通用白名单过滤**丢掉了 `output`/`kernel`/`hint`/
  `stdout_spill_path`**（`tool_results.py:582-636`）→ 模型可能看不到自己打印的内容。
  需先确认生产实际走哪条车道。
- (c) **超时元信息撒谎**：超时杀掉内核后，下一次调用报 `state_reset=false`
  （`kernel.py:303-308`），与工具描述"元信息总是说真话"（`tool.py:46-47`）矛盾。
- 最小改法：给 execute_code 加一条 compaction 分支（保留 `status/error/hint/kernel` +
  stdout head/tail）；修 `state_reset`；确认并修 chat 车道的 key 过滤。

### G5 无资源上限 / 无文件系统边界

- 事实：`_spawn` 没有 `preexec_fn`、没有 rlimit、没有 fs jail；cell 与后端**同 UID**，
  可读写任意绝对路径（`kernel.py:458-499`；设计文档认可这点：不是监狱）。
  别的执行路径已经在用 rlimit（`app/services/terminal/resource_limiter.py:32-50`）。
- 最小改法：`_spawn` 加 `preexec_fn` + rlimits（内存/CPU/进程数/文件大小），
  可选地给"写"加 cwd 白名单。
- 验收：`while True` / 巨内存 cell 不再能拖垮 agent 进程。

---

## 3. 路由：从"文案引导"到"显式升级契约"

已做（2026-09-26 同日）：

- 工具文案对称化：`code_executor` 不再自称 `PRIMARY TOOL`、不再点名 harness；
  `execute_code` 明确认领"单脚本"主场（commit `1bdfd843`）。
- 硬守卫 `DELEGATION_TOO_SMALL`：无 plan 绑定的单脚本委派**拒一次**并指向 `execute_code`
  （commit `60583d7d`）。

下一步：内核返回**结构化失败种类**（`missing_dependency | needs_isolation | too_long |
multi_file`），主循环据此升级到 `code_executor`，而不是让模型靠猜。这是 §0 判定
成立后的最后一环。

---

## 4. 评测盲区：先补"pi 强项"的任务（本规划的第 1 步）

现有 28 个任务全是单脚本 ⇒ 补 4 类、每类至少 2 个任务，均带机器可判定的 check：

1. **多文件实现**：3+ 个文件互相 import、要同时改两处才过。
2. **需要装包**：依赖不在环境里，必须走受控安装通道（G2）。
3. **长流程**：单次 > 5 分钟，或必须分阶段并保留状态（G3）。
4. **必须迭代调试**：第一次运行必然报错，要读 traceback 再修（考察错误回灌质量 G4）。

判定方式：这 4 类任务在 code-mode 扩强**前后**分别对 pi 做对照，看成功率/耗时/兜底率。

---

## 5. 明确不做

- 不做内存/CPU 完全隔离（v1 与 Hermes 一致，只做 wall-clock + 输出 cap + 调用预算）。
- 不把 code-mode 变成"另一个 pi"（不引入它自己的 LLM 循环；它就该是"你写 Python"）。
- **不建议现在退役 pi**——先过 §0 的判定；也不建议现在把 `.8` 永久锁死在 `qwen_code`
  （那会掩盖我们要测的差距）。
