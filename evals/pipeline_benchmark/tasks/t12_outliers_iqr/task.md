uploads/ 目录下有测量数据 measurements.csv（列：样本号,测量值）。
请用 1.5×IQR 规则（下界 = Q1 - 1.5×IQR，上界 = Q3 + 1.5×IQR，四分位数按线性插值计算）
找出所有离群点，把离群点保存到 results/outliers.csv（列：样本号,测量值，不要输出索引列），
并在回复里告诉我离群点的数量。
