# 高级评估系统 v2.0

一个功能强大的内容评估和质量管理系统，支持多种先进的评估模式和自监督机制。

## 🚀 核心特性

### 🧠 多种评估模式
- **LLM智能评估**: 基于大语言模型的深度语义理解
- **多专家评估**: 5位专业领域专家的协作评估
- **对抗性评估**: 生成器与批评者的对抗性改进
- **元认知评估**: 评估质量的自我反思和认知偏见检测
- **领域专业评估**: 针对噬菌体研究的专业评估

### ⚡ 性能优化
- **多层缓存系统**: 内存 + SQLite持久化缓存
- **智能缓存策略**: 基于使用频率的自动清理
- **性能监控**: 实时性能统计和优化建议

### 🔍 质量监督
- **自监督机制**: 自动检测评估系统质量下降
- **实时监控**: 多维度质量指标监控
- **自动校准**: 基于历史数据的阈值调整
- **警报系统**: 多级别警报和推荐措施

## 📚 文档导航

### 🏃‍♂️ 快速开始
- **[快速开始指南](QUICK_START.md)** - 5分钟快速上手
- **[示例代码](../examples/evaluation_examples.py)** - 完整的使用示例

### 📖 详细文档
- **[完整使用指南](EVALUATION_SYSTEM_GUIDE.md)** - 详细的功能说明和最佳实践
- **[API参考文档](API_REFERENCE.md)** - 完整的编程接口文档

## 🎯 快速开始

请参考 Quick Start 文档中的评估相关命令与示例：`docs/QUICK_START.md`

## 🔧 运行示例

更多示例代码与脚本，请参见 `docs/QUICK_START.md` 与 `examples/` 目录。

## 📊 系统架构

```
评估系统架构
├── 执行层 (executor_enhanced.py)
│   ├── 基础评估执行
│   ├── LLM智能评估执行
│   ├── 多专家评估执行
│   └── 对抗性评估执行
├── 评估器层 (services/evaluation)
│   ├── LLM评估器 (llm_evaluator.py)
│   ├── 多专家评估器 (expert_evaluator.py)
│   ├── 对抗性评估器 (adversarial_evaluator.py)
│   ├── 元认知评估器 (meta_evaluator.py)
│   └── 噬菌体专业评估器 (phage_evaluator.py)
├── 优化层
│   ├── 缓存系统 (evaluation/evaluation_cache.py)
│   └── 监督系统 (evaluation/evaluation_supervisor.py)
└── 接口层 (cli/commands/evaluation_commands.py)
    └── CLI命令支持
```

## 🎨 评估模式选择

| 内容类型 | 推荐模式 | 特点 |
|----------|----------|------|
| 简单文档 | 基础评估 | 快速、高效 |
| 专业内容 | LLM智能评估 | 深度理解、智能建议 |
| 重要文档 | 多专家评估 | 多角度验证、专业意见 |
| 关键内容 | 对抗性评估 | 最高质量、鲁棒性强 |
| 科研论文 | 噬菌体专业评估 | 领域专业、术语准确 |

## 📈 性能指标

- **评估准确性**: > 85%
- **系统响应时间**: < 10秒 (缓存命中)
- **缓存命中率**: > 60%
- **系统可用性**: > 99%

## 🛠️ 技术栈

- **Python 3.8+**
- **SQLite** (持久化缓存)
- **大语言模型** (智能评估)
- **多线程** (并发处理)
- **CLI框架** (命令行接口)

## 📋 功能清单

### ✅ 已完成功能

- [x] 基础评估系统
- [x] LLM智能评估器
- [x] 多专家评估系统 (5位专家)
- [x] 对抗性评估机制
- [x] 元认知评估系统
- [x] 噬菌体专业领域评估器
- [x] 多层缓存系统 (内存+持久化)
- [x] 评估质量自监督机制
- [x] 完整CLI命令支持
- [x] 性能优化和监控
- [x] 详细文档和示例

### 🔄 持续改进

- [ ] 更多领域专业评估器
- [ ] 分布式评估支持
- [ ] Web界面
- [ ] 更多语言模型支持

## 🚨 故障排除

### 评估速度慢？
```bash
# 检查缓存状态
python -c "from app.services.evaluation.evaluation_cache import get_evaluation_cache; print(get_evaluation_cache().get_cache_stats())"

# 优化缓存
python -c "from app.services.evaluation.evaluation_cache import get_evaluation_cache; get_evaluation_cache().optimize_cache()"
```

### 评估质量不稳定？
```bash
# 查看监督报告
python -m cli.main --eval-supervision --detailed

# 检查系统统计
python -m cli.main --eval-stats --detailed
```

### 系统错误？
```bash
# 重置监督状态
python -c "from app.services.evaluation.evaluation_supervisor import get_evaluation_supervisor; get_evaluation_supervisor().reset_supervision_state()"
```

## 📞 技术支持

遇到问题？按以下顺序排查：

1. **查看快速开始**: [docs/QUICK_START.md](docs/QUICK_START.md)
2. **运行示例验证**: `python examples/evaluation_examples.py`
3. **检查系统状态**: `python -m cli.main --eval-supervision --detailed`
4. **查看详细文档**: [docs/EVALUATION_SYSTEM_GUIDE.md](docs/EVALUATION_SYSTEM_GUIDE.md)

## 🤝 贡献指南

欢迎贡献代码和建议！

### 开发环境设置
```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
python -m pytest tests/

# 运行示例
python examples/evaluation_examples.py --example all
```

### 代码规范
- 遵循PEP 8代码风格
- 添加类型注解
- 编写单元测试
- 更新相关文档

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 🏆 版本历史

### v2.0.0 (当前版本)
- ✨ 新增LLM智能评估器
- ✨ 新增多专家评估系统
- ✨ 新增对抗性评估机制
- ✨ 新增元认知评估系统
- ✨ 新增噬菌体专业评估器
- ✨ 新增多层缓存系统
- ✨ 新增自监督质量控制
- 🚀 完善CLI命令支持
- 📚 完整文档和示例

### v1.0.0
- ✅ 基础评估系统
- ✅ 评估历史管理
- ✅ 基础CLI支持

---

**高级评估系统 v2.0** - 让内容评估更智能、更准确、更可靠

*最后更新: 2024年*
