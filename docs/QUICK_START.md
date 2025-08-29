# 评估系统快速开始指南

## 5分钟快速上手

### 1. 基础评估 (最简单)

```bash
# 执行基础评估
python -m cli.main --eval-execute 123 --threshold 0.8

# 查看评估结果
python -m cli.main --eval-history 123
```

### 2. LLM智能评估 (推荐)

```bash
# 使用AI智能评估
python -m cli.main --eval-llm 123 --threshold 0.8 --max-iterations 3

# 带上下文的智能评估
python -m cli.main --eval-llm 123 --use-context --threshold 0.85
```

### 3. 多专家评估 (高质量)

```bash
# 所有专家协作评估
python -m cli.main --eval-multi-expert 123 --threshold 0.8

# 选择特定专家
python -m cli.main --eval-multi-expert 123 --experts "clinical_physician,regulatory_expert"
```

### 4. 对抗性评估 (最严格)

```bash
# 生成器vs批评者对抗评估
python -m cli.main --eval-adversarial 123 --max-rounds 3
```

### 5. 系统监控

```bash
# 查看系统健康状态
python -m cli.main --eval-supervision

# 查看详细监控报告
python -m cli.main --eval-supervision --detailed

# 查看评估统计
python -m cli.main --eval-stats --detailed
```

## 运行示例

```bash
# 运行所有示例
python examples/evaluation_examples.py --example all

# 运行特定示例
python examples/evaluation_examples.py --example llm
python examples/evaluation_examples.py --example multi-expert
python examples/evaluation_examples.py --example adversarial
```

## 基准评测（Benchmark）

```bash
# 运行多配置基准评测（CLI）
conda run -n LLM python -m cli.main --benchmark \
  --benchmark-topic "抗菌素耐药" \
  --benchmark-configs "base,use_context=False" "ctx,use_context=True,max_chars=3000,semantic_k=5" \
  --benchmark-sections 5 \
  --benchmark-outdir results/抗菌素耐药 \
  --benchmark-csv results/抗菌素耐药/summary.csv \
  --benchmark-output results/抗菌素耐药/overview.md

# 通过 REST API 触发
curl -X POST http://127.0.0.1:8000/benchmark \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "抗菌素耐药",
    "configs": ["base,use_context=False", "ctx,use_context=True,max_chars=3000,semantic_k=5"],
    "sections": 5
  }'
```

说明：
- **--benchmark-outdir**: 每个配置生成的 Markdown 报告输出目录
- **--benchmark-csv**: 汇总各配置的维度均值/平均分/耗时等为统一 CSV
- **--benchmark-output**: 基准评测总览 Markdown（表格汇总）

## 常用参数

| 参数 | 说明 | 默认值 | 推荐值 |
|------|------|--------|--------|
| `--threshold` | 质量阈值 | 0.8 | 0.7-0.8 |
| `--max-iterations` | 最大迭代次数 | 3 | 3-5 |
| `--use-context` | 使用上下文 | false | true |
| `--max-rounds` | 对抗轮数 | 3 | 3-5 |
| `--experts` | 选择专家 | all | 根据需要 |

## 评估模式选择指南

| 内容类型 | 推荐模式 | 原因 |
|----------|----------|------|
| 简单文档 | 基础评估 | 快速、高效 |
| 专业内容 | LLM智能评估 | 深度理解 |
| 重要文档 | 多专家评估 | 多角度验证 |
| 关键内容 | 对抗性评估 | 最高质量 |
| 科研论文 | 噬菌体专业评估 | 领域专业 |

## 质量阈值建议

- **0.6-0.7**: 草稿阶段
- **0.7-0.8**: 正式文档
- **0.8-0.9**: 重要内容
- **0.9+**: 关键文档

## 故障排除

### 评估速度慢？
```bash
# 检查缓存状态
python -c "from app.services.evaluation_cache import get_evaluation_cache; print(get_evaluation_cache().get_cache_stats())"

# 优化缓存
python -c "from app.services.evaluation_cache import get_evaluation_cache; get_evaluation_cache().optimize_cache()"
```

### 评估质量不稳定？
```bash
# 查看监督报告
python -m cli.main --eval-supervision --detailed

# 检查一致性指标
python -m cli.main --eval-stats --detailed
```

### 系统错误？
```bash
# 重置监督状态
python -c "from app.services.evaluation_supervisor import get_evaluation_supervisor; get_evaluation_supervisor().reset_supervision_state()"

# 清理缓存
python -c "from app.services.evaluation_cache import get_evaluation_cache; get_evaluation_cache().clear_cache()"
```

## 下一步

- 阅读完整文档: [`docs/EVALUATION_SYSTEM_GUIDE.md`](EVALUATION_SYSTEM_GUIDE.md)
- 运行示例代码: [`examples/evaluation_examples.py`](../examples/evaluation_examples.py)
- 查看API参考: 文档中的API参考部分

## 技术支持

遇到问题？
1. 查看监督报告: `--eval-supervision --detailed`
2. 检查系统统计: `--eval-stats --detailed`
3. 运行示例验证: `python examples/evaluation_examples.py`