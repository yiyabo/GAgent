# Phage-Agent

Phage-Agent 是面向生物医学研究的 AI 任务编排系统，支持对话式工具调用、分层计划树、后台任务执行，以及文件产物的浏览与管理。

## 仓库与版本

| 仓库 | 用途 |
| --- | --- |
| [GitHub · yiyabo/GAgent](https://github.com/yiyabo/GAgent) | 开发与集成仓库，`main` 接收经 PR 和 CI 检查的更新。 |
| [OneDev · ms2025/bioagent](https://git.byoryn.cn/ms2025/bioagent.git) | 独立的生产部署与发布仓库，使用该仓库的发布标签选择部署版本。 |

两个仓库独立维护，不自动同步。GitHub `main` 不能直接视为生产部署版本；部署与升级应以目标仓库的发布记录、配置和镜像为准。

生产仓库的初始独立发布标签为 `v2026.10.17`，对应提交 `df7a3672a811fc3e2426415990e6975939a3dd15`。该版本与本仓库提交 `ff81332c51e8d74bba842aa32d5cff8d32ccd0e7` 的源码树相同，保留了两个仓库的历史；后续发布请查看生产仓库。

## 代码导航

| 路径 | 内容 |
| --- | --- |
| [`app/`](app/) | Python 后端、对话控制、计划与任务执行服务。 |
| [`tool_box/`](tool_box/) | Agent 工具实现及生物信息工具接入。 |
| [`web-ui/`](web-ui/) | React / TypeScript 前端。 |
| [`deploy/`](deploy/) | Docker 镜像、Compose 配置及部署运维脚本。 |
| [`app/tests/`](app/tests/) | 后端测试。 |
| [`evals/`](evals/) | 评测用例与评测脚本；各套件的范围见其 README。 |

## 开发与验证

后端要求 Python 3.10 或以上，依赖由 [`pyproject.toml`](pyproject.toml) 和 [`uv.lock`](uv.lock) 管理。前端 CI 使用 Node.js 22，通过 `npm ci` 安装锁定依赖。容器部署配置见 [`deploy/docker-compose.yml`](deploy/docker-compose.yml)。

下面的命令与现有 CI 的主要回归检查一致，不需要真实模型调用：

```sh
# 在仓库根目录安装后端依赖并执行回归
uv sync --frozen --no-dev --python 3.12
TIKTOKEN_CACHE_DIR="$PWD/deploy/tiktoken-cache" \
  uv run --frozen --no-dev --python 3.12 python -m pytest app/tests -q -m "not external"

# 前端检查
cd web-ui
npm ci
npm run type-check
npm run test -- --run
```

完整检查定义见 [CI workflow](.github/workflows/ci.yml)。PR 执行 Python 3.10、Python 3.12 和前端回归；合入 `main` 后还执行容器栈验证与沙箱镜像构建。测试通过与真实任务效果分别记录，真实模型评测需单独配置额度。

## 维护约定

- 更新通过 PR 合入 `main`，三个回归检查通过且分支与主线同步后才能合并；管理员同样受保护规则约束。
- `main` 禁止强推和删除；已合并的开发分支自动删除，提交历史保留在主线。
- 未合并的评测与开发工作需单独审查，不能因分支较旧就删除或直接合并。
- 访问凭据、真实 `.env`、数据库、任务工作目录、用户文件和内部交接记录不提交到公开仓库。配置模板见 [`.env.example`](.env.example) 与 [`deploy/.env.template`](deploy/.env.template)。
- 保留依赖锁文件、离线分词缓存和评测输入。清理历史分支前保存可验证的引用与备份，不重写既有提交历史。
