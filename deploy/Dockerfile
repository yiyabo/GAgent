# bioagent 主应用镜像
# 基础环境用 uv 按 pyproject.toml + uv.lock 精确还原依赖；
# 依赖装到 /opt/venv（UV_PROJECT_ENVIRONMENT），开发期把源码挂到 /app 不会遮住虚拟环境。
FROM docker.byoryn.cn/library/python:3.12-slim

# 容器以 root 运行：接入项目文件空间后需要 CAP_CHOWN 做写后属主修正
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# docker-cli：容器内经挂载的 docker.sock 管理 code_executor 等 sidecar；
# procps：孤儿进程清理要用的 ps
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli procps \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# 先只拷依赖清单，利用层缓存：源码改动不触发重新装依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY tool_box ./tool_box
COPY skills ./skills
COPY scripts ./scripts

EXPOSE 9000

# 探活用 python 标准库（slim 镜像没有 curl）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
