# PhageScope MCP 服务设计（含现有噬菌体生信系统评估）

状态：设计稿，待 w 审阅。只读调研 live（p83），未改动任何线上配置。

## 1. 现状评估

### 1.1 现有组成

| 层 | 组件 | 现状 |
|---|---|---|
| 数据 | PhageScope PostgreSQL（`phage` 库） | 2,576,926 个噬菌体、27 个数据集、1.315 亿蛋白、1,261 万蛋白家族、7 类功能注释轨道 |
| 数据服务 | `gagent-phagescope-data`（host，`127.0.0.1:18326`，经 forwarder 暴露到 `172.20.0.1`） | `phage_ro` 只读角色 + 启动时权限自检、10 s 语句超时、4 连接池；v1 GET 路由 + v2 POST 研究级路由 |
| 加速层 | Parquet 宽表 v2（`phagescope-accel/current`）、家族 FTS5 索引 + 每日漂移检查 | 有 manifest、SHA-256、行数核对、原子切换；"未知 ≠ 0" 语义写进了数据 |
| Agent 工具 | `phagescope`（旧网站 API + taskid）、`phagescope(action=gateway)`（v1）、`phagescope_data`（v2，12 个 operation） | 三套入口并存 |
| 计算 | `bio_tools`（34 个工具，native Slurm runner）、PhageScope 网站分析（phageapi） | 独立分析网关 `PHAGESCOPE_GATEWAY_ANALYSIS_URL` 仍为空 |
| MCP | 对内：`mcp_mount` + 扩展目录（fetch、arxiv，加固容器 + 出网白名单）；对外：`tool_box/mcp_server.py`（stdio，只暴露 `sequence_fetch`、`load_skill`） | 对外 MCP 不含任何 PhageScope 能力 |

近 7 天数据服务调用：`v2/proteins/query` 4,793 次、`v2/phages/query` 555、`v2/features/query` 214、`availability` 169、`aggregate` 117、`detail` 95、`cohort_report` 60，v1 GET 合计约 150。实际负载几乎全在 v2。

### 1.2 评价

| 维度 | 评价 | 依据 |
|---|---|---|
| 数据规模与独占性 | 强 | 257 万噬菌体 + 1.3 亿蛋白 + 家族/结构/注释一体，且数据库与服务在同一台机器。公开 MCP 生态里没有同量级的噬菌体数据源。 |
| 数据安全 | 强 | 只读角色、权限自检、语句超时、常量时间比较 key、只绑内网。 |
| 数据可信度表达 | 强 | 快照 manifest、未知计数为 null 而非 0、家族索引新鲜度报告、"零命中 ≠ 临床安全" 的说明。这是多数生信 agent 做不到的。 |
| 可复现性 | 中上 | Parquet/FTS 版本不可变、可回滚；但 `stats/overview` 的 `checked_on` 仍是 2026-09-11，响应里没有统一的数据版本字段。 |
| 工具面清晰度 | 弱 | 同一数据有三套工具（旧网站 taskid、gateway v1、phagescope_data v2），描述长，模型需自己判断用哪个；`proteins/query` 7 天 4,793 次，提示存在重复/循环调用，值得单独排查。 |
| 计算能力 | 中 | 本机 bio_tools + Slurm 很全，但 PhageScope 自有分析模块仍走公网网站 API，独立分析网关未配置；缺少"拿我的序列在 PhageScope 里找相似噬菌体/蛋白"这一最核心的检索能力。 |
| 对外开放 | 缺失 | 数据服务只在机器内网可达，单一共享 key，没有按用户的身份、配额和审计；外部 agent（Codex、Claude Code 等）无法使用。 |

结论：数据层和安全/可信度设计是这个系统最强的部分，已经达到可对外服务的质量；短板在"入口"——对内工具重叠，对外完全没有入口。MCP 服务正好补这块，而且应当直接复用 v2 数据服务，不另起一套数据访问。

## 2. 目标与非目标

目标：
1. 让外部 agent（Codex、Claude Code、WorkBuddy、Hermes、Grok、Devin）通过标准 MCP 使用 PhageScope 数据。
2. 只读、按用户鉴权、限额、可审计；每个结果带数据版本和截断/未知标记。
3. 与 GAgent 内部 `phagescope_data` 共用一份契约（schema + client），避免第四套入口。

非目标（本期）：任意 SQL、写操作、批量全库导出、替代 PhageScope 网站。

## 3. 架构

```
外部 agent ──HTTPS(Streamable HTTP, Bearer)──> Apache phageagent.deepomics.org/mcp
                                              └─> gateway(nginx) /mcp ──> gagent-phagescope-mcp 容器
                                                                              │ X-API-Key（服务端持有）
                                                                              └─> 172.20.0.1:18326 数据服务（不变）──> PostgreSQL phage_ro / Parquet / FTS
```

- 新服务 `gagent-phagescope-mcp`：官方 `mcp` SDK（已锁定 2.3.0）的 Streamable HTTP 服务器，独立容器，无状态，可水平扩展；不连数据库，只调用现有数据服务。数据服务的共享 key 只在这个容器里，绝不下发给客户端。
- 本地 stdio 模式：同一包提供 `python -m phagescope_mcp --stdio`，内部转发到远端 HTTP，给只支持 stdio 的客户端用。
- 代码位置：`app/services/phagescope_mcp/`（server、tools、auth、provenance），复用 `tool_box/tools_impl/phagescope_data/{client,schema}.py`；工具的输入校验和 `phagescope_data` 共用一套定义。
- 路由：优先 `https://phageagent.deepomics.org/mcp`（复用现有证书和 vhost，只在 gateway nginx 加一个 location）。

## 4. 鉴权、限额与审计

- 个人访问令牌（PAT）：GAgent 登录用户在设置页生成，库里只存哈希；scope 分 `data:read`（默认）和 `analysis:submit`（三期，单独申请）。可吊销、可设过期。
- 限额：按令牌的每分钟请求数、每日返回字节数、并发数；超限返回 MCP 错误 + `retry_after`。数据服务本身保留 10 s 语句超时作为最后一道闸。
- 审计：每次调用记录令牌 id、工具、参数摘要、耗时、返回行数和字节，不记录令牌明文。
- OAuth 2.1：Claude.ai/ChatGPT 网页版连接器需要；放到四期。Codex、Claude Code、Hermes、Devin 用 Bearer 头即可。

## 5. 工具面

原则：按研究任务设计，而不是把 20 个 HTTP 路由一一映射；每个工具有 `outputSchema`，返回 `structuredContent` + 简短文本摘要；全部标注 `readOnlyHint=true`。

一期（只读数据，11 个）：

| 工具 | 作用 | 对应数据服务 |
|---|---|---|
| `catalog_overview` | 总量、数据集、数据版本/快照时间 | `stats/overview`、`datasets` |
| `search_phages` | 按 accession/数据集/完整度/生活方式/分类/宿主/簇/长度/GC 过滤，游标分页 | `v2/phages/query` |
| `cohort_report` | 一次返回计数、长度统计、多维分面和样例 | `v2/phages/cohort_report` |
| `aggregate_phages` | 1–3 维分组统计 | `v2/phages/aggregate` |
| `get_phage` | 单个噬菌体完整元数据 + 6 级宿主谱系 + 文件可用性 | `v2/phages/detail`、`availability` |
| `get_sequence` | 基因组/蛋白序列，默认上限 20 万碱基；超出时返回资源链接 | `phages/{id}/sequence` + 文件 |
| `search_proteins` | 按噬菌体、产物关键词、功能类、蛋白 id 批量查 | `v2/proteins/query` |
| `search_families` / `family_members` | 家族关键词（FTS/同义词/字面）与成员 | `v2/proteins/query`、`families/*` |
| `query_features` | tRNA、CRISPR、抗 CRISPR、终止子、毒力、耐药、跨膜 | `v2/features/query` |
| `get_structures` | ESMFold/AFDB 可用性与 pLDDT，PDB 走资源链接 | `v2/structures/resolve` |

资源（MCP resources）：`phagescope://phage/{accession}/{fasta|faa|gff3}`、`phagescope://protein/{id}/pdb`。大文件返回 15 分钟有效的签名下载链接，单文件和每日总量受令牌限额约束。

提示模板（MCP prompts）：`explore_cohort`（宿主/生活方式队列探索）、`characterize_phage`（单个噬菌体画像）、`find_safety_markers`（毒力/耐药/溶原标记，自带"零命中不等于安全"的说明）。

二期：`similarity_search`——用户给序列，用 MMseqs2 在 PhageScope 蛋白/基因组上找相似项（本机已有 mmseqs2 和 Slurm）。需要先建索引并评估资源占用，是最有价值的新能力。

三期：`analysis_submit` / `analysis_status` / `analysis_results`，复用已有 gateway 契约（18 个模块、`submission_unknown` 回执语义），仅 `analysis:submit` scope 可见，按令牌限并发。依赖独立分析网关先配置好。

## 6. 返回契约

每个结果都带：

```json
{"provenance": {"source": "postgres|parquet:v2|fts:v1", "snapshot_built_at": "...", "service_version": "pN"},
 "page": {"cursor": "...", "has_more": true, "total": 159693},
 "limits": {"truncated": false, "max_bases": 200000},
 "notes": ["CRISPR/终止子计数在 22,720 个重复 accession 上为 null（未知），不是 0"]}
```

- 列表默认 20 条、上限 50；文本摘要不超过 ~8k 字符，完整数据在 `structuredContent`。
- 错误统一为 `{code, message, hint}`，例如 `missing_filter`、`too_broad`（给出建议的收窄维度）、`rate_limited`、`family_index_unavailable`。
- 预测类字段（宿主、生活方式、完整度）注明来源工具，避免外部 agent 当作实验事实。

## 7. 测试与验证

- 单测：mock 数据服务，覆盖每个工具的 schema、分页、截断、未知值、错误码、鉴权和限额。
- 协议：MCP Inspector + 官方 SDK 客户端跑 `initialize`/`tools/list`/`tools/call`/`resources/read`。
- 隔离环境：按已有隔离栈方案起测试实例，指向只读数据服务，不碰 live。
- 客户端试用：固定 12 个任务（例如"列出宿主为 Salmonella、完整度 Complete 的烈性噬菌体数量及长度分布"、"找含抗 CRISPR 蛋白的 Pseudomonas 噬菌体"、"给出 NC_001416 的基因组和 pLDDT>80 的蛋白结构"），在 6 个客户端各跑一遍，记录成功率、调用次数、错误和体验问题，输出对比报告。

## 8. 客户端接入（接入时逐个实测，以下为预期写法）

| 客户端 | 方式 |
|---|---|
| Codex | `~/.codex/config.toml`：`[mcp_servers.phagescope]`，`url = "https://phageagent.deepomics.org/mcp"`，`bearer_token_env_var = "PHAGESCOPE_TOKEN"` |
| Claude Code | `claude mcp add --transport http phagescope https://phageagent.deepomics.org/mcp --header "Authorization: Bearer $PHAGESCOPE_TOKEN"` |
| WorkBuddy | `mcpServers` JSON 配置远程 URL + headers；不支持远程时用 stdio 转发模式 |
| Hermes Agent | `~/.hermes/config.yaml` 的 `mcp_servers` 填 url + headers |
| Grok | xAI API 的远程 MCP 工具（server_url + authorization）；Grok 网页/机器人入口需另查 |
| Devin | 组织 MCP 设置里添加自定义 HTTP 服务器 + Authorization 头 |

## 9. 上线步骤

1. 设计审阅（本文）。
2. 一期 PR：MCP 服务 + PAT + 限额 + 审计 + 测试；隔离栈验证。
3. 打 pN 镜像，经夜间队列正常发布；gateway 新增 `/mcp`。不改数据服务和数据库。
4. 6 个客户端试用 + 对比报告。
5. 二期相似性检索、三期分析提交、四期 OAuth 和公开目录登记。

## 10. 待 w 决定（括号内为默认）

1. 对外地址：`phageagent.deepomics.org/mcp` 还是独立子域名（默认前者）。
2. 谁能拿令牌：所有 GAgent 注册用户，还是白名单（默认先白名单，试用后放开）。
3. 外部用户能否下载完整基因组/蛋白文件，以及每日上限（默认允许，单令牌每日 2 GB）。
4. 内部三套 PhageScope 工具是否借此收敛为 `phagescope_data` + 分析提交两套（默认是，单独 PR）。
