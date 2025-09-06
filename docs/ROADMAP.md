# Roadmap / TODO

本文档跟踪系统的规划路线，按短/中/长期拆解。

## 短期（S）

- S1: 交互式对话任务构建系统
  - 会话式 Goal→Plan，所见即所得预览
  - 多步细化与即时评估提示
  - 支持“一步生成/逐步完善”两种流

- S2: Agent 效果评测基线
  - 统一评测框架与指标（质量/事实性/效率/成本）
  - Across‑Models 对比：GPT / Claude / Gemini / Grok 等
  - 任务集、评分维度与统计出具（CSV/MD/仪表盘）

- S3: Pydantic v2 迁移与清理
  - 使用 `ConfigDict` + `pydantic-settings`，消除弃用告警
  - 测试覆盖补齐（评估/检索/计划/执行关键路径）

## 中期（M）

- M1: 噬菌体外源知识图谱构建
  - 领域 Schema 与实体/关系抽取
  - 引证与可追溯；与上下文/检索联动
  - 结合 Tool Box 的抓取/清洗/入库流水线

- M2: 评估监督可视化
  - Dashboard（Supervision / Cache / 执行统计）
  - 历史对比、阈值调优、预警订阅

- M3: 兼容别名清退
  - 去除 `services/__init__.py` 旧路径别名
  - 文档与示例统一新分层导入路径

## 长期（L）

- L1: 多 Agent 协同与角色分工
  - 策划/执行/评估/审校分离与博弈
  - 自适应工具策略与计划调整

- L2: 插件化生态与多租户
  - 评估器/检索器/执行器插件机制
  - 企业级配置中心与租户隔离

## 注意事项

- 新增特性优先复用现有分层（foundation/context/evaluation/planning），避免横切跨层依赖。
- 统一通过 `foundation/settings.py` 注入配置，严禁分散读取环境变量。

