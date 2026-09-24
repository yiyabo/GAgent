#!/bin/bash
# bioagent 镜像构建脚本（与 bioapi/tgrpc 的构建方式一致）。
# docker build 自带层缓存：依赖清单没变时 uv sync 层直接命中，只有源码层重算，天然增量。
# 用法：./build.sh [--push]
set -e

PUSH=false
for arg in "$@"; do
    case "$arg" in
        --push) PUSH=true ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE="docker.byoryn.cn/ms/biomedical"

echo "==> Building docker image (${IMAGE}:latest) ..."
cd "$PROJECT_ROOT" && docker build -f deploy/Dockerfile -t "${IMAGE}:latest" .

if [ "$PUSH" = true ]; then
    echo "==> Pushing image to registry ..."
    docker push "${IMAGE}:latest"
fi

echo "==> Done!"
