"""bioagent 本地开发启动入口。

与 start_backend.sh（conda 时代遗留，保持原样不再改动）并行的独立入口：
- 解释器契约：用哪个 python 运行本文件就用哪个解释器，脚本不感知环境管理器——
  uv 用户 `uv run python run.py`，venv 用户直接 `.venv/bin/python run.py`，
  conda 用户先 activate。
- 配置全部来自环境变量 / bioagent/.env，变量名与旧脚本保持一致，迁移零成本。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

ROOT_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """加载 .env；用 python-dotenv 而不是手写解析，
    引号、行内注释、转义等边界行为与生态标准一致。override=False
    表示真实环境变量优先，不覆盖调用方显式设置。"""
    load_dotenv(ROOT_DIR / ".env", override=False)


def _apply_optional_proxy() -> None:
    """可选出网代理（默认关）：部分网络下 LLM 网关需要代理才可达。
    大小写变量同时设置，兼容不同 http 客户端的读取习惯。"""
    enabled = os.getenv("BACKEND_PROXY_ON", "false").lower() in ("1", "true", "yes", "on")
    if not enabled:
        return
    proxy = os.getenv("BACKEND_PROXY_URL", "http://127.0.0.1:7890")
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ[name] = proxy
    all_proxy = os.getenv("BACKEND_ALL_PROXY") or proxy
    os.environ["all_proxy"] = all_proxy
    os.environ["ALL_PROXY"] = all_proxy


def main() -> None:
    _load_dotenv()
    _apply_optional_proxy()

    # uvicorn 的 reload 子进程要求工作目录在仓库根，否则 app 包导入不到
    os.chdir(ROOT_DIR)

    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "9000"))
    reload_enabled = os.getenv("BACKEND_RELOAD", "true").lower() in ("1", "true", "yes", "on")
    access_log = os.getenv("ACCESS_LOG", "false").lower() in ("1", "true", "yes", "on")

    # reload 排除必须用相对模式：新版 uvicorn 对以 / 开头的模式会抛
    # NotImplementedError（pathlib.glob 不支持非相对模式）
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=["app", "tool_box"],
        reload_includes=["app/**/*.py", "tool_box/**/*.py"],
        reload_excludes=["runtime", "data", "*.db", "*.sqlite"],
        access_log=access_log,
    )


if __name__ == "__main__":
    main()
