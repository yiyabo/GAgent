uploads/sensors.csv 有 3200 行传感器数据（列：sensor_id,reading）。

请用 execute_code 完成：
1. 读取该文件，先打印一行 BEGIN-DUMP，然后逐行打印全部数据行（格式 "sensor_id,reading"），最后打印一行 END-DUMP；
2. 输出会超过 execute_code 的 stdout 上限而被截断——这是预期行为。被截断后不要重跑全量打印：完整 stdout 会自动保存到 spill 文件（结果里会给出路径），kernel 变量也还在，用更窄的输出继续即可；
3. 计算 reading 的均值，按 MEAN=<保留四位小数> 的格式打印并写在回复里。
