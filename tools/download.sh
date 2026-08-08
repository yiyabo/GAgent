#!/usr/bin/env bash
# 下载 Python 依赖 wheel 到 tools/pkgs/wheelhouse，供离线/弱网环境安装。
#
# 用法:
#   bash tools/download.sh            # 精简集（requirements-ci.txt，不含 torch，约 150MB）
#   bash tools/download.sh --full     # 全集（requirements.txt，含 torch，约 2-3GB）
#   bash tools/download.sh --dest DIR # 自定义输出目录
#   bash tools/download.sh --mirror tsinghua|aliyun   # 国内镜像（直连 PyPI 慢/不可用时）
#   bash tools/download.sh --index URL                # 自定义 PyPI 索引
#
# 目标机离线安装:
#   pip install --no-index --find-links tools/pkgs/wheelhouse -r requirements.txt
#
# 注意: wheel 与平台绑定，下载机与目标机的 OS/架构/Python 小版本需一致。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ="$ROOT_DIR/requirements-ci.txt"
DEST="$ROOT_DIR/tools/pkgs/wheelhouse"
INDEX_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)
      REQ="$ROOT_DIR/requirements.txt"
      shift
      ;;
    --dest)
      DEST="$2"
      shift 2
      ;;
    --mirror)
      case "$2" in
        tsinghua) INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple" ;;
        aliyun)   INDEX_URL="https://mirrors.aliyun.com/pypi/simple/" ;;
        *) echo "未知镜像: $2（可选 tsinghua|aliyun）" >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --index)
      INDEX_URL="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\}\s\?//'
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$REQ" ]]; then
  echo "找不到依赖文件: $REQ" >&2
  exit 1
fi

PIP_INDEX_ARGS=()
if [[ -n "$INDEX_URL" ]]; then
  PIP_INDEX_ARGS=(-i "$INDEX_URL")
  echo "PyPI 索引: $INDEX_URL"
fi

mkdir -p "$DEST"
echo "依赖清单: $REQ"
echo "输出目录: $DEST"
python3 -m pip download --requirement "$REQ" --dest "$DEST" "${PIP_INDEX_ARGS[@]}"

echo ""
echo "完成。拷贝 $DEST 到目标机后执行:"
echo "  pip install --no-index --find-links ${DEST#$ROOT_DIR/} -r $(basename "$REQ")"
