# PhageScope 序列相似性检索设计（MMseqs2）

状态：设计稿，待 w 审阅。审阅通过前不建索引、不改代码、不动线上；PhageScope 数据始终只读。

## 1. 目标

用户给一条或多条序列，在 PhageScope 全库里找相似的蛋白和噬菌体，并把命中直接连到已有的元数据、家族、注释和结构。同一能力在三处可用：

- GAgent 内部：`phagescope_data` 新增 `similarity_search` 操作，DeepThink 和计划执行可直接调用。
- 外部 MCP：`https://phageagent.deepomics.org/mcp` 新增 `similarity_search` / `get_similarity_result`（PhageScope MCP 设计第 5 节的"二期"）。
- 数据服务：只读 HTTP 接口，供以上两处调用。

这是 2026-11-20 设计对比报告列出的第一缺口：NCBI、UniProt、EBI 都有序列检索，PhageScope 目前只能按 ID、关键词和过滤条件查。

## 2. 现状（2026-11-20 在 phage-platform 上只读核对）

| 项 | 数值 / 位置 |
|---|---|
| 噬菌体基因组 | 2,576,926 条，合计 53.7 Gbp，平均 39.7 kb；代表基因组（`is_rep`）555,901 条，18.6 Gbp |
| 蛋白 | 131,509,307 条（其中 59,538,098 条标为低质量） |
| 去重蛋白 `unique_seq` | 45,401,092 条，平均 241 aa，P99 1,307 aa，P99.9 2,315 aa，约 10.9 G 氨基酸 |
| 蛋白 → 去重序列 | `unique_seq_member(protein_id, seq_md5, dataset, seq_len)`，`seq_md5` 有索引；`seq_md5` = 去掉末尾 `*` 后序列的 MD5（已用一条 MetaVR 蛋白验证） |
| 蛋白 → 家族 | `phage_protein_family_member(protein_id → family_id, subfamily_id, phage_accession_id, product, function_class)`，`protein_id` 唯一索引 |
| 蛋白序列文件 | `/data2/platform/phage_database/phage_sequence/proteins/`：14 个合并 FASTA（约 17 GB）+ 9 个按噬菌体分文件的目录；表头第一个字段就是 `protein_id` |
| 机器 | ss620b：128 核（AVX-512），1 TB 内存（约 800 GB 可用，多为页缓存），无 GPU |
| Slurm | 单节点 ss620b，分区 `work`/`highmem`（不限时）、`test`（1 h），可分配 100 CPU；当前负载约 3–4 |
| 磁盘余量 | `/data3` 4.8 TB、`/data6` 4.9 TB、`/data4` 694 GB、`/data2` 9.5 GB（已满，不能放新文件） |
| MMseqs2 | 17.b804f（`/data3/platform/genefamily/envs/genefamily/bin/mmseqs`）；环境模块 `MMseqs2/13-45111`（bio_tools native 模式在用） |
| 已有结构检索 | PhageScope 网页分析有 `structure_search`（TM-Vec）和 `structure_search_foldseek`（Foldseek，库为每个非单例家族一个结构，2,160,766 条，13 GB）；GAgent 的 18 个分析模块里没有这两个 |

结论：蛋白侧的数据已经是"去重 + 有 MD5 + 有家族映射"的状态，建库主要是抽取和校验，不需要重算任何注释。

## 3. 检索对象与方法

### 3.1 一期：蛋白 → 蛋白（MMseqs2）

- 目标库：45.4 M 条去重蛋白（`unique_seq`），每条以 `seq_md5` 为键。命中后再展开到成员蛋白、噬菌体和家族。比直接建 131.5 M 条库小 2.9 倍，结果也不会被同一序列的重复拷贝刷屏。
- 方法：`mmseqs search`（预建索引，`--db-load-mode 2` 常驻页缓存），默认 `-s 5.7`、`-e 1e-3`、每条查询最多 500 个目标，输出 BLAST 风格列 + qcov/tcov。
- 选 MMseqs2 不选 BLAST/DIAMOND：MMseqs2 已在本机两套环境里、PhageScope 的家族聚类本来就用它（参数可对齐）、同等灵敏度下比 BLAST 快两个数量级；DIAMOND 也可行，但再引入一套工具没有收益。
- 低质量蛋白：默认一并检索，结果里标 `low_quality=true`，可用 `exclude_low_quality` 过滤。

### 3.2 二期：基因组 → 相似噬菌体（基于共享蛋白）

用户给一条噬菌体基因组（≤ 500 kb）：先用 prodigal-gv 预测蛋白（本机已有），再走一期蛋白检索，最后按噬菌体聚合：共享蛋白数、共享比例、平均一致性、加权分数，返回前 N 个噬菌体，并给出它们的簇/亚簇。这和 vConTACT 等噬菌体分类工具"按共享基因判断亲缘"的思路一致，不需要另建核酸库。

### 3.3 三期（可选，单独评估后再定）

- 核酸 ANI：用 skani 对 555,901 个代表基因组建草图库，回答"ANI ≥ 95% 的同种噬菌体"。全库 53.7 Gbp 建 MMseqs2 核酸索引约需 TB 级内存，不考虑。
- 结构检索：把已有的 Foldseek 家族结构库（2.16 M）作为 `structure_search` 接入 GAgent 和 MCP，输入 PDB 或用 ProstT5 从序列推 3Di。PhageScope 侧已经在跑，GAgent 只需接入。

## 4. 资源估算

估算依据：MMseqs2 官方文档的索引内存公式 M ≈ 7 × 残基数 + 8 × 21^k 字节（k=6 时第二项约 0.7 GB），以及本机核对的实际规模。所有时间数字是估算，必须在试点中实测。

| 项 | 估算 |
|---|---|
| 抽取 45.4 M 条去重序列为 FASTA | ≈ 11 GB；顺序扫 17 GB 合并 FASTA + 9 个分文件目录，单进程约 1–3 h（IO 为主） |
| MMseqs2 序列库（createdb） | ≈ 13 GB（序列 + 表头 + 索引文件） |
| 预建索引（createindex，k=6） | 磁盘与常驻内存 ≈ 80 GB；32 线程约 1–2 h |
| 建库临时空间 | ≈ 150 GB，建完删除 |
| 落盘总计 | ≈ 100 GB/版本，保留当前 + 上一版本 ≈ 200 GB，放 `/data3/platform/gagent/phagescope-similarity/` |
| 单条蛋白查询（16 线程，索引已在内存） | 预计几秒到几十秒 |
| 一个基因组（约 60 个蛋白）查询 | 预计 1–3 min |
| 并发 | 全局同时最多 2 个检索作业 × 16 CPU，占 Slurm 100 CPU 中的 32 |

内存是主要成本：80 GB 索引常驻页缓存，约占本机内存 8%。若与 PostgreSQL 等争缓存导致查询变慢，退路是按 `--split` 分片检索，或降为家族代表序列库（12.6 M 条，约 3 G 残基，索引约 21 GB），代价是需要再从家族展开成员。

## 5. 架构

```
用户/外部 Agent ──► GAgent (phagescope_data / MCP similarity_search)
                         │  HTTP（服务端 key，仅 127.0.0.1）
                         ▼
             PhageScope 数据服务（systemd 用户服务，宿主机）
                 │ 校验查询、查缓存、建作业目录
                 ▼
             sbatch（work 分区，16 CPU / 32 GB / 10 min）
                 │ mmseqs search → m8
                 ▼
             数据服务读取结果 → 用 PostgreSQL 只读展开成员/家族/噬菌体 → JSON
```

要点：

- 检索作业放在数据服务后面，而不是在 GAgent 容器里跑：索引、Slurm 和只读数据库都在宿主机，GAgent 容器继续只通过 HTTP 访问数据服务，网络边界不变。
- 作业走 Slurm：与 PhageScope 网页分析、bio_tools native runner 用同一套资源记账和取消（`scancel`）。数据服务只渲染固定模板的 sbatch 脚本，查询序列只写入作业目录里的 FASTA 文件，不进命令行。
- 结果缓存：键为（索引版本、规范化后查询序列的 SHA-256、参数），保留 7 天；同一查询重复提交直接返回缓存。
- 查询序列是用户数据：审计只记录序列数、总长度和哈希，不记录序列本身；作业目录 7 天后清理。

### 5.1 数据服务接口（新增，只读）

- `POST /agent-api/v1/data/v2/similarity/search`：提交。参数 `sequences`（FASTA 文本或 `[{id, seq}]`）、`mode`（`protein`，二期加 `genome`）、`max_hits`（默认 50，上限 500）、`evalue`（默认 1e-3）、`min_identity`、`min_coverage`、`sensitivity`（`fast`=4 / `default`=5.7 / `sensitive`=7.5）、`exclude_low_quality`、`datasets`。返回 `search_id`；若 20 s 内完成则直接带结果。
- `GET /agent-api/v1/data/v2/similarity/search/{search_id}`：状态与结果（`queued|running|done|failed|expired`）。
- `GET /agent-api/v1/data/v2/similarity/status`：索引版本、条数、构建时间、MMseqs2 版本、队列长度。
- 输入限制：一期每次最多 50 条蛋白、合计 ≤ 50,000 aa，单条 10–10,000 aa；只接受 20 种标准氨基酸 + `BJOUXZ*`，否则 `invalid_sequence`；看起来是核酸时提示改用 `mode=genome`。

### 5.2 结果契约

沿用 PhageScope MCP 第 6 节的 `provenance/page/limits/notes` 外壳，每条命中：

```json
{"query_id": "q1", "seq_md5": "46e6...", "rep_protein_id": "IMGVR_UViG_..._1",
 "identity": 0.62, "aln_len": 201, "qcov": 0.97, "tcov": 0.95, "evalue": 3e-85, "bits": 290,
 "member_count": 3, "phage_count": 3, "datasets": ["MetaVR"],
 "family_id": "...", "family_product": "...", "function_class": "...", "low_quality": false}
```

- `provenance` 增加 `index_version`、`mmseqs_version`、`params`，满足报告里"结论可重放"的要求。
- 成员展开默认只给代表蛋白和计数；需要全部成员时用 `similarity_members(search_id, seq_md5)` 分页（`unique_seq_member.seq_md5` 有索引）。
- `notes` 固定说明：E 值依赖库大小（随快照变化）；未命中不代表 PhageScope 中不存在远缘同源，可调高灵敏度或改用结构检索。

### 5.3 GAgent 内部工具

`phagescope_data` 新增 `similarity_search` 和 `similarity_result` 两个操作：提交后在工具超时内（默认 120 s）轮询；未完成就返回 `search_id`，由 Agent 稍后再查。`materialize` 可把命中蛋白 FASTA 和 m8 原文件写入会话 `results/phagescope/`，供 bio_tools 和 `execute_code` 继续分析（建树、比对等）。

### 5.4 外部 MCP

- `similarity_search`：参数同 5.1；20 s 内完成则直接返回结果，否则返回 `search_id` 和 `poll_after`。`readOnlyHint=true`、`idempotentHint=true`（同参数命中缓存）。
- `get_similarity_result`：取状态与结果，分页。
- 两个工具都带 `outputSchema`，返回 `structuredContent` + 简短文本摘要（设计对比报告 P1 第 2 项，可在此一并落地）。
- 限额：按令牌 1 个并发检索、每日 200 条查询序列；全局队列 20 个，满了返回 `too_busy` + `retry_after`。与现有每分钟请求数、每日字节数限额叠加。

## 6. 建库与更新

1. 抽取：按 `unique_seq.rep_protein_id` 从合并 FASTA 和分文件目录取序列；对每条重算 MD5 并与 `seq_md5` 比对，表头写 `seq_md5`。
2. 校验：条数 = `unique_seq` 条数；MD5 不符或缺失的序列单独列出，超过 0.1% 则构建失败，不切换。
3. 建库：`createdb` → `createindex`（Slurm `highmem`，32 CPU）。
4. 清单：`manifest.json` 写入快照时间、条数、残基数、MMseqs2 版本、参数、各文件 SHA-256。
5. 冒烟：用 20 条已知蛋白自查，期望自身为第一命中、identity=1。
6. 原子切换：`current` 软链接指向新版本，与 `phagescope-accel` / `phagescope-family-search` 同一模式；保留上一版本用于回滚。
7. 新鲜度：沿用 `gagent-phagescope-family-check.timer` 的做法，每周比对 `unique_seq` 条数与索引清单，不一致只告警，不自动重建。

## 7. 测试与验证

- 单测：查询校验（核酸/非法字符/超长/超量）、缓存键、结果展开、错误码、限额；Slurm 和 mmseqs 用桩替代。
- 正确性：选 200 条有家族的蛋白，期望命中自身和同家族成员；与 PhageScope 现有家族成员做召回对比。另取 50 条 PhageScope 外部蛋白（例如 UniProt 中的噬菌体蛋白）与 BLASTp 在同一子库上比对命中一致性。
- 性能：记录单条、10 条、50 条蛋白以及 1 个基因组的 P50/P95 耗时、CPU 和内存，并在 PostgreSQL 有负载时复测。
- 隔离环境：按已有隔离栈方案测试数据服务和 MCP，不碰线上。

## 8. 实施步骤（每步完成后汇报，w 同意后再走下一步）

1. 本设计审阅。
2. 试点：只用 RefSeq + PhagesDB + GPD 子集（约几百万条）建库，实测建库时间、索引大小、查询耗时，修正第 4 节估算。只在 `/data3/platform/gagent/phagescope-similarity/pilot/` 下写文件，不改任何线上服务。
3. 全量建库（约半天，主要是 IO 和 createindex）。
4. PR：数据服务接口 + `phagescope_data` 操作 + MCP 工具 + 测试，默认关闭（`PHAGESCOPE_SIMILARITY_ENABLED=0`）；隔离栈验证。
5. 按 pN 发布流程上线并打开开关；更新 MCP 查询指南资源和客户端示例。
6. 二期：基因组 → 相似噬菌体；三期：skani ANI、Foldseek 结构检索按需求另行评估。

## 9. 待 w 决定（括号内为默认）

1. 外部 MCP 用户是否开放相似性检索，还是先限内部（默认对现有 MCP 令牌开放，按第 5.4 节限额）。
2. 低质量蛋白是否默认参与检索（默认参与并标记，可过滤）。
3. 试点通过后是否直接全量建库（默认是，建完汇报实测数字后再提代码 PR）。
