请用 execute_code 工具完成一个批量检索汇总（不要逐个手动调 web_search）：

在 execute_code 里循环调用 web_search，依次做 3 个检索：
1. bacteriophage lysin clinical trial
2. endolysin antibiotic resistance
3. phage therapy FDA approval

然后在 kernel 里合并结果、按域名去重，汇总并打印：
- 每个检索各返回了多少条结果
- 合并去重后一共覆盖多少个不同域名
- 3 个代表性链接（http/https 完整 URL）

最后在你的回复里给出这三个数字（每检索条数、去重域名总数）和这 3 个链接，并简述你用了 execute_code 的循环来批量完成。
