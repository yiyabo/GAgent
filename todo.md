下次组会：

## 工具调用

1. 用phage scope的数据集用于验证 experiment2 数据集。
2. phage scope提供的工具可以覆盖哪些常用的生物信息学工具
3. 工具列表，phage scope里面有的；sandbox里面有的
4. nc 论文仔细复现，把对应的生物信息工具添加上
5. 阅读csv文件的时候，应该要去使用Claude Code，而不是用document_reader

### PhageScope × DeepPL 联动

1. 先跑 PhageScope 的 Annotation Pipline，至少包含 `quality + lifestyle + annotation + host` 模块，拿到 `taskid`。
2. 任务完成后执行 `save_all`，保存 `metadata / annotation / sequences` 结果到本地目录。
3. 从保存结果导出同一条序列，按 DeepPL 输入要求预处理为单条两行 FASTA（仅 `ACTG`，去掉 `N` 和多序列）。
4. 运行 DeepPL（DNABERT 微调模型）得到 `lytic / lysogenic` 预测标签。
5. 做一致性判断：`PhageScope lifestyle` 与 `DeepPL` 一致记为高置信；不一致记为待复核（重点查 `integrase/repressor` 等证据）。
6. 统一输出对照表：`sample_id, phagescope_lifestyle, deeppl_label, consensus, notes`。
7. 后续补一个自动化脚本，把 `save_all -> DeepPL -> 对照表` 串成一键流程。

## 服务器部署

1. 至少有1-2个实验需要用到phage scope里面的工具
2. 今晚部署上服务器

## 结果解读和可视化

1. "结果解读和可视化"当成一个小的解析任务
2. 基于有价值的部分和整个数据集进行可视化优化
3. Claude Skills嵌入
4. 使用测试数据集进行

## 深度思考

1. 修改深度思考功能的prompt，让其实用不同的工具尽可能覆盖多的场景

## Nature 复现

● 实验A 进度总结

  ONT 长读长链路

| 步骤        | 工具                | 状态                      |
| ----------- | ------------------- | ------------------------- |
| 1. 数据下载 | ENA                 | 完成 (12个文件, ~310GB)   |
| 2. QC评估   | NanoPlot            | 完成                      |
| 3. 去人源   | minimap2 + samtools | ✅ 完成                   |
| 4. 组装     | Flye                | 🔄 进行中 (Polishing阶段) |

  当前 Flye 状态

- 阶段: Polishing (纠错)
- 初步结果: 9,955 contigs, 620 Mb
- 预计输出: assembly.fasta

---

  后续步骤（还未做）

| 步骤            | 工具                       | 说明                  |
| --------------- | -------------------------- | --------------------- |
| 5. 降采样       | seqtk                      | 降到6Gb与Illumina对比 |
| 6. Illumina组装 | MEGAHIT                    | 短读长组装            |
| 7. Binning      | MetaBAT2/CONCOCT/MaxBin2   | 分箱                  |
| 8. MAG评估      | CheckM                     | 质量评估              |
| 9. 分类注释     | GTDB-Tk                    | 物种注释              |
| 10. 噬菌体预测  | geNomad/VIBRANT/VirSorter2 | 噬菌体检测            |

## 合并

1. 合并lly代码（正在进行中）
2. 合并wmh代码
3. 合并yjy代码

## 图表

0. 需要一个完整的对话----------（文件）（解决）
1. pipeline，高度概括的流程图--------图；（借鉴一下人家的图表和数值型的指标，不局限于phage）（解决，有待后续优化）
2. ML复现（结果对比------文件）。ML小文章可以多跑几篇（涵盖要全，还有差异分析、MLE，生物信息上的一些常用的统计分析）----------列一个表格（解决，有待后续优化）
3. 使用生物信息工具（需要方便扩展），bio_tools常见工具表格，测试例子，效果如何（测试结果画一个相关的图表）（解决，有待后续优化）
4. NC的复现，完整对话、概述图（尽量多一些具体的数据指标，可以列的都列上，展现复现分别做了什么，用了什么生物信息工具。以及结果对比。找NC中比较有代表性的实验，对比一下。）
5. DNA BERT举个例子，可以画个图。用这个模型来跑几个phagescope，看看它的效果
6. 扩展已经发表的工作的能力，基于人家的工作继续做扩展。比如DNABERT，可以放一些具体的实验结果图。独立的数据和在phage scope做的验证。
7. 左右脑互搏的过程，比如在哪个问题上分歧很大，引入第三方是如何去解决的，契机是什么，解决出来后最后是偏向于哪一方，这一方是否可以进量化分析以优化prompt提示词。或者让系统自备一套提示词，进入灰度测试，自我迭代进化。

8. 扩展和优化一下其他人的工作
