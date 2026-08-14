#!/bin/bash
# 

echo "======================================"
echo ""
echo "======================================"

# conda
if [ -n "$CONDA_DEFAULT_ENV" ]; then
    echo "✓ conda: $CONDA_DEFAULT_ENV"
else
    echo "⚠️  conda，LLM..."
    source ~/anaconda3/etc/profile.d/conda.sh
    conda activate LLM
fi

echo ""
echo "1. PDF (PyPDF2)..."
pip install PyPDF2

echo ""
echo "2.  (Pillow)..."
pip install Pillow

echo ""
echo "3. OCR (pytesseract) []..."
read -p "OCR？(y/n): " install_ocr

if [ "$install_ocr" = "y" ] || [ "$install_ocr" = "Y" ]; then
    echo "pytesseract..."
    pip install pytesseract
    
    echo ""
    echo "⚠️  : pytesseractTesseract OCR"
    echo ""
    echo "macOS:"
    echo "  brew install tesseract tesseract-lang"
    echo ""
    echo "Linux:"
    echo "  sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim"
    echo ""
    echo "Windows:  https://github.com/UB-Mannheim/tesseract/wiki "
    echo ""
else
    echo "OCR"
fi

echo ""
echo "======================================"
echo "✅ ！"
echo "======================================"
echo ""
echo "："
echo "  • PDF"
echo "  • "
if [ "$install_ocr" = "y" ] || [ "$install_ocr" = "Y" ]; then
    echo "  • OCR"
fi
echo "  • LLM（）"
echo ""
