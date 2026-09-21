# User Journey E2E 验收套件

用 computer use 驱动真实产品界面（agent.medicalheart.cn），模拟用户完成一个
完整项目旅程，逐轮判定检查点。这是四层测试体系（pytest / pipeline_benchmark /
harness_benchmark / 哨兵）之外的**产品级端到端层**：覆盖前端渲染、会话持久性、
意图路由、深想链路、工具调用、交付物展示的全链路真实表现。

## 目录

- `journeys/` — 旅程脚本（每轮话术 + 预期行为 + 检查点 + 常见失败）。现有：
  - `clinical_data_analysis.md` — 医学临床数据分析（二甲双胍与 2 型糖尿病肾功能，T0–T9）
- `fixtures/` — 测试数据与生成器（确定性种子，真值见旅程附录）。
- `runs/` — 执行产物（截图 + 记录表），**不入 git**（本目录已 gitignore）。

## 执行要点

1. 测试账号：临时注册（旅程 T0 顺带验证注册链路）；账号信息不写入仓库。
2. 每轮：逐字输入话术 → 等待最终答复完成 → 对照检查点判定 → 截图存 `runs/<日期>/`。
3. 上传 fixture：T5 经界面附件功能上传 `fixtures/diabetes_cohort.csv`。
4. 判定纪律：看到检查点要求的具体证据才算过（plan_id、URL、真值数字、内联图、
   交付物可打开），"感觉还行"不算过。
5. 后端交叉验证（可选）：会话落盘在 .8 `/data/phage-agent/runtime/<session_id>/`，
   可核对 deliverables/ 与报告内容。

## 添加新旅程

复制 `journeys/clinical_data_analysis.md` 的结构：T0 环境校验 → 模糊开场 →
需求确认 → 任务构建 → 执行（工具密集）→ 交付审核 → 持久性回看。真值类检查点
必须有可独立复算的基准（fixture 生成器打印真值）。
