# 依赖清单与离线部署

本目录登记 Phage-Agent 运行所需的全部软件/包，并提供离线安装包的下载脚本。
机器可读版本见 `manifest.tsv`；下载脚本见 `download.sh`。

## 1. 运行时要求

| 名称 | 版本 | 必需性 | 用途 |
|---|---|---|---|
| Python | ≥ 3.10（CI 用 3.10） | 必需 | 后端运行时 |
| SQLite | 3.x（Python 内置） | 必需 | 主存储（已启用 WAL） |
| Node.js | ≥ 18 | 前端开发/E2E | web-ui 构建、Playwright |
| Docker | 20.10+ | 可选 | 代码执行沙箱（`app/services/interpreter/docker_interpreter.py`） |
| npm | ≥ 9 | 可选 | JS 运行时校验（`runtime_guardrails.py`） |
| qwen CLI | — | 可选 | qwen_code_runtime（`interpreter/task_executer.py`） |
| bibtex | — | 可选 | 文献引用导出（`artifact_routes.py`） |

## 2. Python 依赖（requirements.txt，31 个直接依赖）

| 领域 | 包 | 版本 | 用途 / 使用位置 |
|---|---|---|---|
| Web 框架 | fastapi / starlette / uvicorn[standard] | ≥0.111 / ≥0.37 / ≥0.30 | API 服务入口 `app/main.py` |
| 数据校验/配置 | pydantic / pydantic-settings / email-validator | ≥2.8 / ≥2.0 / ≥2.0 | 全站模型与 `foundation/settings.py` |
| 配置加载 | python-dotenv / PyYAML | ≥1.0 / ≥6.0 | `.env` 与 YAML 配置 |
| HTTP 客户端 | httpx / socksio / requests / aiohttp | ≥0.23 / ≥1.0 / ≥2.31 / ≥3.8 | LLM 调用（httpx 连接池）、embedding、代理支持 |
| 数据处理 | numpy / pandas / scipy | ≥1.26 / ≥2.0 / ≥1.10 | 向量与分析计算 |
| 图算法 | networkx | ≥3.0 | 轻量使用（执行器提示词示例） |
| ML（重，约 2–3GB） | torch / sentence-transformers | ≥2.0 / ≥2.7 | 本地嵌入回退 `embeddings/local_embedding_client.py`；**CI 不安装** |
| 存储/总线 | redis | ≥5.0 | 实时事件总线 `realtime_bus.py`（SQLite 为内置主存储） |
| 安全 | argon2-cffi / asyncssh | ≥23.1 / ≥2.14 | 口令哈希（`auth.py`/`sso.py`）、SSH 终端后端 |
| 文档处理 | pypdf / mammoth / Pillow / reportlab / markdown | 见 requirements | PDF/DOCX 读取、图片、PDF 生成、Markdown 渲染 |
| 执行沙箱 | docker（Python SDK） | ≥6.0 | 沙箱容器编排 `docker_interpreter.py` |
| 系统监控 | psutil | ≥5.9 | `/system/health` 资源指标 |
| 文件上传 | python-multipart | ≥0.0.9 | 上传接口 |
| 测试 | pytest / pytest-asyncio | ≥7.0 / ≥0.21 | 单元测试（CI 门禁） |

## 3. 前端依赖（web-ui/package.json）

| 领域 | 主要包 |
|---|---|
| 框架 | react 18 / react-dom / react-router-dom / vite |
| UI | antd 5 / @ant-design/icons / framer-motion / classnames / dayjs |
| 可视化 | three / three-spritetext / react-force-graph-2d/3d / vis-network / vis-data |
| 编辑器 | monaco-editor / @monaco-editor/react |
| 终端 | xterm / xterm-addon-fit / xterm-addon-web-links |
| Markdown/公式 | react-markdown / markdown-to-jsx / remark-gfm / remark-math / rehype-katex / katex / prismjs / react-syntax-highlighter |
| 状态/请求 | zustand / @tanstack/react-query / axios / socket.io-client / lodash-es |
| 测试/E2E | @playwright/test / @testing-library/jest-dom（devDependencies） |

完整版本约束以 `web-ui/package.json` 与 `package-lock.json` 为准。

## 4. Docker 镜像（自行构建，不在 Docker Hub）

| 目录 | 用途 | 构建 |
|---|---|---|
| `docker/code_executor` | 代码执行沙箱 | `docker build -t phage-code-executor docker/code_executor` |
| `docker/qwen_code_runtime` | qwen 代码运行时 | `docker build -t phage-qwen-runtime docker/qwen_code_runtime` |

## 5. 外部服务（不可安装，仅登记）

| 服务 | 配置位置 | 说明 |
|---|---|---|
| LLM 网关（sub2api / moma） | `.env` 的 `LLM_*` | OpenAI 兼容协议 |
| 嵌入 API | `.env` 的 `QWEN_*/EMBEDDING_*` | 远程嵌入，本地回退见上 |
| 平台 SSO | `.env` 的 `SSO_*` | 用户同步与登录回跳 |

## 6. 离线 / 弱网部署

```bash
# 在有网的机器上生成 wheelhouse（默认精简集，不含 torch，约 150MB）
bash tools/download.sh

# 需要本地嵌入回退时下载全集（含 torch，约 2–3GB）
bash tools/download.sh --full

# 拷贝 tools/pkgs/wheelhouse 到目标机后离线安装
pip install --no-index --find-links tools/pkgs/wheelhouse -r requirements.txt
```

注意：

- `tools/pkgs/` 已 gitignore，实体 wheel 不进仓库。
- wheel 与平台/Python 版本绑定：下载机与目标机的 OS、架构、Python 小版本需一致（如都是 linux x86_64 + CPython 3.11）。**最简单的做法：直接在目标机（如 110）上运行本脚本。**
- 需要交叉下载时（下载机与目标机平台不同），显式指定目标平台：

  ```bash
  python3 -m pip download -r requirements.txt --dest tools/pkgs/wheelhouse \
    --platform manylinux2014_x86_64 --python-version 311 --only-binary=:all:
  ```

  注意此模式要求所有依赖都有预编译 wheel（无 wheel 的包会报错）。
- 前端 `node_modules` 无离线包机制：有网执行 `npm ci`，或将整个 `node_modules`/`npm cache` 打 tarball 拷贝。
