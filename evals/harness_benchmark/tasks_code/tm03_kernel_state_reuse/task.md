这是一个两轮任务，考察 execute_code 的 kernel 变量复用。

第 1 轮：uploads/series.csv 有 60 行数值（列 value）。请用 execute_code 读取它，计算 value 的均值 mean_v 和标准差 std_v，保存在 kernel 变量里（下一轮还要接着用）。这一轮先只告诉我 mean_v（保留两位小数）。

第 2 轮（同一会话稍后发出）：直接复用 kernel 里已有的 mean_v 和 std_v（不要重新读取 series.csv），用 execute_code 计算有多少个数据点落在 [mean_v - std_v, mean_v + std_v] 闭区间之外，把个数告诉我。
