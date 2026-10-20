# 自我进化评测（evolution）

目的：在隔离的 620b 评测实例上，用模拟用户和确定性检查测试 Agent 的真实表现（单步工具、多轮追问、纠正、失败恢复、能力边界、计划执行），把问题整理成交接包，交给后续的策略设计与代码修改。

## 组成

| 文件 | 作用 |
|---|---|
| `controller.py` | Mac 侧控制器：构造每个场景的清单（夹具、oracle、判分标准）、经 SSH 把 `driver.py` 注入评测容器执行、回拉产物、记账。 |
| `driver.py` | 单文件驱动，只用标准库和应用内的聊天运行函数。在容器里经 stdin 执行，清单不落盘。 |
| `scenarios.py` | 公开场景 S01–S25（夹具、oracle、检查项、模拟用户脚本）。 |
| `fixtures.py` | 带种子的合成数据生成器，同一场景每次字节一致。 |
| `instance/` | 隔离评测实例（compose、环境派生脚本、runner 服务、运行说明）。 |
| `runs/` | 本地原始产物与预算账本（不入库，见 `.gitignore`）。 |

盲测场景（B01–B06）放在仓库之外的私有目录（默认 `/Users/apple/code/evolution-private/blind/`），其定义、夹具与 oracle 不进入仓库，也不进入交接包，只输出汇总。

## 运行

```sh
python3 evals/evolution/controller.py list
python3 evals/evolution/controller.py run --round pilot1 --only S01,S02 --dry-run   # 只构造清单，不联网
python3 evals/evolution/controller.py run --round pilot1 --only S01,S02            # 真实运行，消耗 token
python3 evals/evolution/controller.py summarize --round pilot1
```

每个场景开始前，控制器检查账本：已用量加本场景上限若超过停止线（默认 3,600,000 token）就跳过。用量未知时按上限计入，不记为零。

## 判定

- 确定性检查：`run_ok`、`no_fallback`、`tool_used`、`number_line`（数字必须出现在含标签词的行里）、`number_any`、`consistent_count` / `consistent_n50`（与 Agent 生成的文件逐一核对）、`file_records`、`file_exists`、`no_new_files`、`terms_any` / `terms_none`、`turns_max`。
- 主观项：`judge` 用 `qwen3.7-max` 按评分标准打 0–2 分，只看转录与参考事实。
- 模拟用户：`qwen3.8-flash`，只看到助手对用户可见的回复，按人设和目标生成下一条消息，或回复 `DONE`。有明确纠正步骤的场景使用脚本化的用户轮次。
- 召回关闭（`memory_enabled=false`），场景文本要求只用上传文件。

## 产物

每个场景写入 `/evo-out/<轮次>/<场景>/`（服务器上 `~/gagent-eval/evo-out`）：`result.json`（检查结果、用量、可见回复）、`transcript.md`（转录）、`events.jsonl`（运行事件）。控制器把公开场景拉回 `evals/evolution/runs/<轮次>/`，盲测拉回私有目录。

## 已知限制

- 单次运行有随机性；本轮预算不足以重复多次，结论按单次结果报告。
- 判分也是模型，需要用试点结果校准（见交接包的判分复核部分）。
- 评测容器的 Docker 代理与线上一样未开启策略强制，见 `instance/README.md` 的残留风险。
