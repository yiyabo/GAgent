# A-mem集成快速开始

## 🎯 什么是A-mem？

A-mem (Agentic Memory) 是一个智能记忆系统，已集成到Claude Code执行流程中，用于：

- 📚 **记录执行经验** - 自动保存每次Claude Code的执行结果
- 🔍 **智能检索** - 为新任务提供相关的历史参考
- 🧠 **知识演化** - 自动建立记忆间的语义链接
- 📈 **持续学习** - 执行越多，经验越丰富

## 🚀 快速启动

### 1. 配置A-mem

**好消息：A-mem会自动使用你现有的GLM API密钥！**

配置文件已经设置好，无需修改：

```bash
cd execute_memory/A-mem-main
cat config.cfg
# 已配置使用 glm-4-flash
# 会自动读取环境变量 GLM_API_KEY
```

如果你想使用其他LLM，可以编辑 `config.cfg`。

### 2. 启动A-mem服务

```bash
# 使用启动脚本（推荐）
bash scripts/start_amem.sh

# 或手动启动
cd execute_memory/A-mem-main
python api.py --port 8001
```

### 3. 启用A-mem集成

在 `.env` 文件中添加：

```bash
AMEM_ENABLED=true
AMEM_URL=http://localhost:8001
```

### 4. 重启后端

```bash
bash start_backend.sh
```

## ✅ 验证

```bash
# 测试A-mem服务
curl http://localhost:8001/health

# 运行集成测试
python scripts/test_amem_integration.py
```

## 💡 使用示例

现在当你使用Claude Code时，A-mem会自动工作：

```
用户: "请训练一个噬菌体预测模型"

→ A-mem查询相似任务的历史经验
→ LLM获得增强的上下文
→ Claude Code执行任务
→ 结果自动保存到A-mem
```

第二次执行类似任务时，LLM会自动获得第一次的经验！

## 📖 详细文档

查看 [docs/AMEM_INTEGRATION.md](docs/AMEM_INTEGRATION.md) 了解更多信息。

## 🔧 故障排除

**A-mem服务无法启动？**

1. 检查config.cfg中的API密钥
2. 确保安装了依赖：`cd execute_memory/A-mem-main && pip install -e .`

**集成不工作？**

1. 确认 `.env` 中 `AMEM_ENABLED=true`
2. 检查A-mem服务是否运行：`curl http://localhost:8001/health`
3. 查看后端日志中的 `[AMEM]` 标记

## 🎉 完成！

现在你的系统已经具备了记忆和学习能力！
