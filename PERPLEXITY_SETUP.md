# Perplexity API 配置指南

## 🎯 **快速配置**

### 1. 创建 `.env` 文件
在项目根目录创建 `.env` 文件：

```bash
# LLM Provider Configuration  
LLM_PROVIDER=perplexity

# Perplexity API Configuration
PERPLEXITY_API_KEY=your_actual_perplexity_api_key_here
PERPLEXITY_API_URL=https://api.perplexity.ai/chat/completions
PERPLEXITY_MODEL=llama-3.1-sonar-small-128k-online

# General Settings
LLM_MOCK=false
LLM_RETRIES=3
DATABASE_URL=sqlite:///./tasks.db
BASE_URL=http://127.0.0.1:8000
```

### 2. 获取Perplexity API密钥
1. 访问 [Perplexity API Settings](https://www.perplexity.ai/settings/api)
2. 登录或创建账户
3. 生成新的API密钥
4. 复制密钥并替换上面的 `your_actual_perplexity_api_key_here`

### 3. 可用模型
- `llama-3.1-sonar-small-128k-online` (推荐，联网搜索)
- `llama-3.1-sonar-large-128k-online` (更强性能)
- `llama-3.1-sonar-huge-128k-online` (最强性能)
- `llama-3.1-8b-instruct` (快速离线)
- `llama-3.1-70b-instruct` (平衡离线)

### 4. 测试配置
```bash
# 启动后端
conda activate LLM
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 新终端启动聊天
conda activate LLM
python -m cli.main --chat
```

## 💡 **切换回GLM**
只需修改 `.env` 文件：
```bash
LLM_PROVIDER=glm
GLM_API_KEY=your_glm_api_key
```

## 🔧 **环境变量优先级**
1. 命令行参数 (`--provider perplexity`)
2. 环境变量 (`LLM_PROVIDER=perplexity`)
3. .env文件设置
4. 系统默认 (glm)
