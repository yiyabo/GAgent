#!/bin/bash
set -e

echo "=========================================="
echo "  更新 oh-my-openagent 和 opencode"
echo "=========================================="
echo ""

if ! command -v bun &> /dev/null; then
    echo "未检测到 bun，正在安装..."
    curl -fsSL https://bun.sh/install | bash
    export PATH="$HOME/.bun/bin:$PATH"
    echo "bun 安装完成"
else
    echo "bun 已安装: $(bun --version)"
fi

echo ""

if ! command -v npm &> /dev/null; then
    echo "错误: 未检测到 npm，请先安装 Node.js"
    exit 1
fi

echo "npm 已安装: $(npm --version)"
echo ""

echo "正在更新 opencode..."
npm update -g opencode-ai
echo "opencode 更新完成"
echo ""

echo "正在更新 oh-my-openagent..."
npm update -g oh-my-opencode
echo "oh-my-openagent 更新完成"
echo ""

echo "=========================================="
echo "  更新完成！当前版本："
echo "=========================================="
echo ""

if command -v opencode &> /dev/null; then
    echo "opencode: $(opencode --version)"
fi

if command -v oh-my-opencode &> /dev/null; then
    echo "oh-my-opencode: $(oh-my-opencode --version 2>/dev/null || echo '已安装')"
fi

echo ""
echo "所有工具已更新完成！"
