uploads/ 目录下有 6 个批次文件 batch_01.csv 到 batch_06.csv（列：record_id,group,value,status）。

请用 execute_code 工具批量处理（在 kernel 里循环读入全部 6 个文件，不要一个个手动读）：
1. 只保留 status == "ok" 且 value > 50 的记录；
2. 统计：6 个文件的总数据行数（不含表头）、过滤后保留的记录数；
3. 按 group 分组，给出保留记录的 value 均值（保留两位小数）。

把总数据行数、保留记录数、以及每个 group 的均值都写在回复里。
