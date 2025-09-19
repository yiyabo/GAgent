# 🚀 双引擎智能聊天系统使用指南

## 🎯 **系统概览**

我们的智能聊天系统现在支持两个强大的AI引擎，各自专长不同场景：

### **🌐 Perplexity引擎**
- **专长**: 实时信息查询、知识问答、趋势分析
- **特色**: 自动联网搜索、引用来源、最新信息
- **模型**: `sonar-pro` (实时联网搜索模型)

### **🛠️ GLM引擎** 
- **专长**: 工具调用、任务执行、结构化操作
- **特色**: 10种专业工具、任务管理、文件操作
- **模型**: 可配置GLM模型 (如`GLM-4.5-Air`) + Tavily搜索API

---

## 🚀 **快速开始**

### **启动聊天系统**
```bash
# 1. 启动后端 (如果未运行)
conda activate LLM
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &

# 2. 启动聊天
python -m cli.main --chat
```

### **基本命令**
```bash
/help              # 查看完整帮助
/switch perplexity # 切换到Perplexity模式
/switch glm        # 切换到GLM工具模式
/clear             # 清除对话历史
/exit              # 退出聊天
```

---

## 💡 **使用场景示例**

### **🌐 Perplexity模式 - 信息查询**
```bash
you> /switch perplexity
✅ 已切换到 PERPLEXITY 引擎
💡 Perplexity模式: 智能对话 + 自动联网搜索

you> 今天AI领域有什么重要新闻？
AI> [自动搜索最新信息] 根据今天的最新报道[1][2][3]...

you> 解释一下GPT-4和Claude的区别
AI> [融合多源信息] GPT-4和Claude是两个主要的大语言模型...

you> 最新的iPhone发布了吗？价格如何？
AI> [实时搜索] 根据苹果官网和科技媒体的最新消息...
```

### **🛠️ GLM工具模式 - 任务执行**
```bash
you> /switch glm  
✅ 已切换到 GLM 引擎
🛠️ GLM模式: 工具调用 + 结构化搜索

you> 添加待办：学习Rust编程语言
AI> [调用add_todo工具] ✅ 已添加待办事项："学习Rust编程语言"

you> 搜索：最新机器学习论文
AI> [调用web_search工具]
   [显示结构化搜索结果表格]
   
you> 帮我制定学习Python的计划
AI> [调用propose_plan工具] 我来为您制定详细的Python学习计划...

you> 保存这个代码到文件
AI> [调用save_content_to_file工具] ✅ 内容已保存到文件...
```

---

## 🔧 **高级功能**

### **智能模式选择**
系统会在启动时显示当前模式，并提供智能切换建议：

```bash
🌐 Perplexity模式已激活
特色功能: 实时信息查询、知识问答、趋势分析
适合场景: "今天AI有什么新闻？" "解释量子计算" "最新疫情情况"
💡 需要工具操作？输入 /switch glm
```

### **无缝切换**
在同一会话中可以随时切换引擎：
```bash
you> 今天股市怎么样？
[Perplexity回答最新股市信息]

you> /switch glm
you> 添加待办：关注今天提到的股票
[GLM执行工具调用]

you> /switch perplexity  
you> 这只股票的技术分析如何？
[Perplexity提供最新分析]
```

---

## 🛠️ **可用工具列表** (GLM模式)

### **任务管理**
- `add_todo` - 添加待办事项
- `list_todos` - 查看待办列表
- `complete_todo` - 完成待办事项

### **信息搜索**
- `web_search` - Tavily结构化搜索
- `intent_router` - 意图路由分析

### **计划执行**
- `propose_plan` - 提出计划
- `visualize_plan` - 可视化计划
- `decompose_task` - 分解任务
- `execute_atomic_task` - 执行原子任务

### **文件操作**
- `save_content_to_file` - 保存内容到文件

---

## ⚡ **性能对比**

| 功能 | Perplexity | GLM+Tavily |
|------|------------|-------------|
| **实时信息** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **工具调用** | ❌ | ⭐⭐⭐⭐⭐ |
| **任务执行** | ❌ | ⭐⭐⭐⭐⭐ |
| **知识问答** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **文件操作** | ❌ | ⭐⭐⭐⭐⭐ |
| **响应速度** | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **信息准确性** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |

---

## 🔧 **配置说明**

### **环境变量设置**
```bash
# 默认使用Perplexity
LLM_PROVIDER=perplexity
PERPLEXITY_API_KEY=your_perplexity_key

# GLM配置 (可选)
GLM_API_KEY=your_glm_key

# Tavily搜索 (可选，增强GLM搜索能力)
TAVILY_API_KEY=your_tavily_key
```

### **切换默认引擎**
```bash
# 修改.env文件
LLM_PROVIDER=glm  # 或 perplexity
```

---

## 💡 **最佳实践**

### **🌐 何时使用Perplexity**
- ✅ 需要最新信息时
- ✅ 知识性问答
- ✅ 趋势分析
- ✅ 事实核查
- ✅ 新闻查询

### **🛠️ 何时使用GLM**
- ✅ 需要执行具体任务
- ✅ 文件操作
- ✅ 待办管理
- ✅ 计划制定
- ✅ 结构化搜索

### **💡 混合使用策略**
1. **信息收集阶段** → Perplexity获取最新信息
2. **任务执行阶段** → GLM执行具体操作
3. **结果验证阶段** → Perplexity验证信息准确性

---

## 🎉 **总结**

双引擎系统让您享受到：
- 🌐 **Perplexity**: 最新信息 + 智能问答
- 🛠️ **GLM**: 强大工具 + 精确执行
- ⚡ **无缝切换**: 一个会话，两种能力
- 🎯 **智能提示**: 自动推荐最佳引擎

开始体验更智能的AI助手吧！🚀
