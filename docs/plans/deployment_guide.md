# GAgent 部署到服务器（公网访问）指南

本文记录将 GAgent（FastAPI + React/Vite）部署到云服务器并开放公网访问的推荐流程。假设代码位于 `/opt/GAgent`，使用 Linux + systemd + Nginx 的常见组合，可根据实际情况微调。

## 1. 环境准备

### 1.1 依赖

- Python 3.10+（推荐使用 venv/conda）
- Node.js 18+ / npm 9+
- Git
- SQLite（系统默认即可）
- Nginx（或其他反向代理/静态服务器）

### 1.2 获取源码
```bash
sudo mkdir -p /opt/GAgent
sudo chown $USER:$USER /opt/GAgent
cd /opt/GAgent
git clone <repo-url> .
```

### 1.3 创建虚拟环境 & 安装依赖
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cd web-ui
npm install
cd ..
```

## 2. 配置统一 `.env`

所有后端与前端配置均放在仓库根目录的 `.env` 中（Vite 会从此处读取）：

```ini
# 后端网络
BACKEND_HOST=0.0.0.0
BACKEND_PORT=9000
CORS_ORIGINS=https://your-frontend-domain.com

# 前端 API 接入地址（Vite 会注入）
VITE_API_BASE_URL=https://your-backend-domain.com
VITE_WS_BASE_URL=wss://your-backend-domain.com

# LLM / Web Search
LLM_PROVIDER=qwen
QWEN_API_KEY=...
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
QWEN_MODEL=qwen-turbo
DEFAULT_WEB_SEARCH_PROVIDER=builtin
BUILTIN_SEARCH_PROVIDER=qwen

# Graph RAG 配置（如需自定义路径）
GRAPH_RAG_TRIPLES_PATH=/opt/GAgent/tool_box/tools_impl/graph_rag/Triples/all_triples.csv

# 其他可选项
PLAN_EXECUTOR_MODEL=...
DECOMP_MODEL=...
```

> 注意：`.env` 已列入 `.gitignore`，请勿提交到版本库。

## 3. 构建前端

```bash
cd /opt/GAgent/web-ui
npm run build
# dist/ 目录即为静态资源，可放置在任意静态服务器或对象存储
```

## 4. 启动后端服务

### 4.1 临时启动（测试）
```bash
cd /opt/GAgent
source venv/bin/activate
./start_backend.sh     # 默认使用 uvicorn --reload
```
确认访问 `http://<服务器IP>:9000/health` 正常。

### 4.2 systemd 配置
创建 `/etc/systemd/system/gagent.service`：

```ini
[Unit]
Description=GAgent Backend
After=network.target

[Service]
WorkingDirectory=/opt/GAgent
EnvironmentFile=/opt/GAgent/.env
ExecStart=/opt/GAgent/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9000
User=www-data
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gagent
sudo systemctl status gagent
```

如需多 worker，可将 `ExecStart` 改用 `gunicorn`：
```ini
ExecStart=/opt/GAgent/venv/bin/gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:9000 --workers 4 --timeout 120
```

## 5. 配置 Nginx（前端 + 反向代理）

示例 `/etc/nginx/sites-available/gagent.conf`：

```nginx
server {
    listen 80;
    server_name your-frontend-domain.com;

    # 前端静态资源
    root /opt/GAgent/web-ui/dist;
    index index.html;
    try_files $uri $uri/ /index.html;

    # API 反向代理
    location /chat/ {
        proxy_pass http://127.0.0.1:9000/chat/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /plans/ {
        proxy_pass http://127.0.0.1:9000/plans/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /tasks/ {
        proxy_pass http://127.0.0.1:9000/tasks/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /health {
        proxy_pass http://127.0.0.1:9000/health;
    }

    # WebSocket（若需要）
    location /ws/ {
        proxy_pass http://127.0.0.1:9000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
    }
}
```

启用并重载：
```bash
sudo ln -s /etc/nginx/sites-available/gagent.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

若需 HTTPS，使用 Certbot/Let’s Encrypt：
```bash
sudo certbot --nginx -d your-frontend-domain.com
```

## 6. 数据与权限

- 确保 `/opt/GAgent/data/`、`example/data/` 等写入目录对运行用户（例如 `www-data`）可写。
- Graph RAG CSV 放在 `GRAPH_RAG_TRIPLES_PATH` 指定位置并授予读取权限。
- 如需 SQLite 备份，可定期复制 `/opt/GAgent/data/databases` 目录。

## 7. 验证

1. 浏览器访问 `https://your-frontend-domain.com` 检查页面是否正常加载。
2. 触发一次聊天，请求 `web_search` / `graph_rag`，确认工具结果在前端展示且后台日志无异常。
3. 调用健康接口：`curl https://your-frontend-domain.com/health`。
4. 检查 `systemctl status gagent` 和 `journalctl -u gagent -f` 确保服务持续运行。

## 8. 常见问题排查

- **CORS 报错**：确认 `.env` 中 `CORS_ORIGINS` 包含前端域名，并重启后端。
- **前端 API 404**：检查 Nginx 反向代理路径是否与 FastAPI 路由前缀一致（默认 `/chat`, `/plans`, `/tasks`）。
- **工具调用失败**：确认 `.env` 中必需的 API Key（如 `QWEN_API_KEY`, `PERPLEXITY_API_KEY`）已配置。
- **Graph RAG 无结果**：检查 CSV 路径是否可被后端读取，若放置自定义位置别忘了修改 `GRAPH_RAG_TRIPLES_PATH`。

## 9. 升级 / 回滚

- `git pull` 更新代码后需重新构建前端、重启后端。
- 建议使用 Git tag 或分支管理稳定版本；升级前备份 `.env` 与 `data/databases`。

---
如需容器化部署，可基于上述步骤编写 Dockerfile 和 Compose，核心思路同样是共享 `.env`、暴露 9000 端口、前端静态资源由 Nginx/前端服务器提供。EOF
