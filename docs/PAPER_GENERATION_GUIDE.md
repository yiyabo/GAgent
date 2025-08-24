# 📚 学术论文生成指南

## 🚀 快速开始

现在您可以用一个简单的命令生成完整的学术论文！

### 方法一：一键生成论文（推荐）

```bash
# 生成因果推理综述论文
python generate_paper.py --topic "因果推理方法综述"

# 生成机器学习综述论文  
python generate_paper.py --topic "深度学习在医学影像中的应用"

# 自定义章节数量
python generate_paper.py --topic "区块链技术综述" --sections 8

# 指定输出文件名
python generate_paper.py --topic "人工智能伦理研究" --output "AI伦理论文.md"
```

### 方法二：使用评估系统手动生成（高质量）

对于需要精细控制的高质量论文，可以使用评估系统分步生成。

## 📊 自动生成过程

1. **自动创建章节**: 根据主题智能生成6个章节
2. **智能内容生成**: 使用LLM评估系统确保质量
3. **质量控制**: 每个章节经过多轮优化（评分阈值0.8）
4. **自动保存**: 生成Markdown格式的完整论文

## 🎯 推荐主题示例

```bash
# 计算机科学
python generate_paper.py --topic "大语言模型发展综述"
python generate_paper.py --topic "联邦学习技术与应用"
python generate_paper.py --topic "计算机视觉前沿方法"

# 医学健康
python generate_paper.py --topic "精准医学研究进展"
python generate_paper.py --topic "基因编辑技术综述"
python generate_paper.py --topic "数字健康技术应用"

# 社会科学
python generate_paper.py --topic "社交媒体对青少年心理健康的影响"
python generate_paper.py --topic "远程工作模式研究"
python generate_paper.py --topic "教育技术创新综述"
```

## 🛠️ 高级选项

### 简单模式（更快，但质量可能较低）
```bash
python generate_paper.py --topic "您的主题" --simple
```

### 自定义章节数量
```bash
python generate_paper.py --topic "您的主题" --sections 10
```

## 📈 生成后的操作

```bash
# 查看生成统计
python -m cli.main --eval-stats --detailed

# 查看系统监督报告
python -m cli.main --eval-supervision --detailed

# 查看特定章节的评估历史
python -m cli.main --eval-history <task-id>
```

## 🎭 手动生成高质量论文（因果推断示例）

### 第一步：查看系统状态
```bash
# 检查评估系统状态
python -m cli.main --eval-supervision

# 查看评估统计
python -m cli.main --eval-stats
```

### 第二步：分步生成论文各部分

#### 1. 摘要部分（使用LLM智能评估）
```bash
python -m cli.main --eval-llm 2001 --threshold 0.85 --max-iterations 5 --use-context --verbose
```
*任务内容示例：*
> "请写一篇关于因果推断方法的综述论文摘要，包括研究背景、主要方法、应用领域和未来方向。"

#### 2. 引言部分（使用多专家评估）
```bash
python -m cli.main --eval-multi-expert 2002 --threshold 0.8 --experts "research_scientist,theoretical_biologist" --max-iterations 4 --verbose
```

#### 3. 核心方法综述（使用对抗性评估，确保最高质量）
```bash
python -m cli.main --eval-adversarial 2003 --max-rounds 5 --improvement-threshold 0.05 --verbose
```

#### 4. 应用领域分析（使用LLM智能评估）
```bash
python -m cli.main --eval-llm 2004 --threshold 0.8 --max-iterations 4 --use-context --verbose
```

#### 5. 挑战与未来方向（使用多专家评估）
```bash
python -m cli.main --eval-multi-expert 2005 --threshold 0.8 --experts "research_scientist,theoretical_biologist,biotech_entrepreneur" --max-iterations 3 --verbose
```

#### 6. 结论（使用LLM智能评估）
```bash
python -m cli.main --eval-llm 2006 --threshold 0.8 --max-iterations 3 --use-context
```

### 第三步：查看生成结果

```bash
# 查看各部分的评估历史和最终内容
python -m cli.main --eval-history 2001
python -m cli.main --eval-history 2002
python -m cli.main --eval-history 2003
python -m cli.main --eval-history 2004
python -m cli.main --eval-history 2005
python -m cli.main --eval-history 2006
```

## 💡 使用技巧

### 1. 选择合适的评估模式
- **摘要、引言、结论**: 使用 `--eval-llm`，快速高效
- **核心方法部分**: 使用 `--eval-adversarial`，确保最高质量
- **应用分析**: 使用 `--eval-multi-expert`，获得多角度视角

### 2. 调整质量参数
- **高质量要求**: `--threshold 0.85 --max-iterations 5`
- **标准质量**: `--threshold 0.8 --max-iterations 3`
- **快速生成**: `--threshold 0.7 --max-iterations 2`

### 3. 专家选择建议
- **理论研究**: `research_scientist,theoretical_biologist`
- **应用导向**: `clinical_physician,biotech_entrepreneur`
- **全面评估**: 使用所有专家（不指定experts参数）

### 4. 对抗性评估参数
- **严格模式**: `--max-rounds 5 --improvement-threshold 0.05`
- **平衡模式**: `--max-rounds 3 --improvement-threshold 0.1`
- **快速模式**: `--max-rounds 2 --improvement-threshold 0.15`

## 📝 输出格式

生成的论文包含：
- ✅ 完整的章节结构
- ✅ 学术化的语言风格
- ✅ 相关的技术细节
- ✅ 实例和案例分析
- ✅ 自动生成的统计信息

## 🔧 故障排除

### 如果生成失败：
1. 确保GLM_API_KEY环境变量已设置
2. 检查网络连接
3. 尝试使用 `--simple` 模式
4. 减少章节数量 `--sections 3`

### 如果评估速度慢：
```bash
# 检查缓存状态
python -c "from app.services.evaluation_cache import get_evaluation_cache; print(get_evaluation_cache().get_cache_stats())"

# 优化缓存
python -c "from app.services.evaluation_cache import get_evaluation_cache; get_evaluation_cache().optimize_cache()"
```

### 如果评估质量不稳定：
```bash
# 查看监督报告
python -m cli.main --eval-supervision --detailed

# 调整监督阈值
python -m cli.main --eval-supervision-config --min-accuracy 0.75 --max-evaluation-time 30.0
```

### 如果需要重新评估：
```bash
# 清除评估历史
python -m cli.main --eval-clear 2001

# 重新执行评估
python -m cli.main --eval-llm 2001 --threshold 0.8 --max-iterations 3
```

## 📊 预期结果

使用这套系统，您将获得：

- ✅ **高质量内容**: 通过多轮迭代和智能评估确保质量
- ✅ **专业视角**: 多专家评估提供不同角度的专业意见
- ✅ **鲁棒性强**: 对抗性评估确保内容经得起批评
- ✅ **实时监控**: 监督系统确保评估质量稳定
- ✅ **性能优化**: 缓存系统提高生成效率

## 🎯 最终整合

### 自动整合（推荐）
使用 `generate_paper.py` 会自动整合所有章节成完整论文。

### 手动整合
如果使用评估系统分步生成，所有部分生成完成后，您可以将各部分内容整合成完整论文。系统会自动保存每个部分的最佳版本，按学术论文标准格式进行最终整理即可。

---

**现在就开始生成您的第一篇高质量学术论文吧！** 🚀