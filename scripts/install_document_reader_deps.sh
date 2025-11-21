#!/bin/bash
# 安装文档读取工具的依赖库

echo "======================================"
echo "安装文档读取工具依赖"
echo "======================================"

# 激活conda环境
if [ -n "$CONDA_DEFAULT_ENV" ]; then
    echo "✓ 已在conda环境: $CONDA_DEFAULT_ENV"
else
    echo "⚠️  未检测到conda环境，尝试激活LLM环境..."
    source ~/anaconda3/etc/profile.d/conda.sh
    conda activate LLM
fi

echo ""
echo "1. 安装PDF读取库 (PyPDF2)..."
pip install PyPDF2

echo ""
echo "2. 安装图片处理库 (Pillow)..."
pip install Pillow

echo ""
echo "3. 安装OCR识别库 (pytesseract) [可选]..."
read -p "是否安装OCR功能？(y/n): " install_ocr

if [ "$install_ocr" = "y" ] || [ "$install_ocr" = "Y" ]; then
    echo "安装pytesseract..."
    pip install pytesseract
    
    echo ""
    echo "⚠️  注意: pytesseract需要系统安装Tesseract OCR引擎"
    echo ""
    echo "macOS安装命令:"
    echo "  brew install tesseract tesseract-lang"
    echo ""
    echo "Linux安装命令:"
    echo "  sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim"
    echo ""
    echo "Windows: 请从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装"
    echo ""
else
    echo "跳过OCR功能安装"
fi

echo ""
echo "======================================"
echo "✅ 依赖安装完成！"
echo "======================================"
echo ""
echo "现在可以使用以下功能："
echo "  • 读取PDF文件"
echo "  • 读取图片文件"
if [ "$install_ocr" = "y" ] || [ "$install_ocr" = "Y" ]; then
    echo "  • OCR文字识别"
fi
echo "  • LLM图片分析（需要支持视觉的模型）"
echo ""
