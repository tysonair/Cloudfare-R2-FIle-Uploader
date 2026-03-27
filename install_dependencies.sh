#!/bin/bash

echo "========================================"
echo "R2 文件上传工具 - 依赖安装脚本"
echo "========================================"
echo ""

echo "[1/4] 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 Python3，请先安装 Python 3.8 或更高版本"
    exit 1
fi
python3 --version
echo "✅ Python 环境检查通过"
echo ""

echo "[2/4] 检查 pip..."
if ! python3 -m pip --version &> /dev/null; then
    echo "❌ 错误: pip 未安装"
    exit 1
fi
echo "✅ pip 可用"
echo ""

echo "[3/4] 升级 pip 到最新版本..."
python3 -m pip install --upgrade pip
echo ""

echo "[4/4] 安装依赖包..."
python3 -m pip install -r requirements.txt
echo ""

if [ $? -eq 0 ]; then
    echo "========================================"
    echo "✅ 所有依赖安装完成！"
    echo "========================================"
    echo ""
    echo "现在可以运行程序了:"
    echo "    python3 r2_uploader_gui.py"
    echo ""
else
    echo "❌ 依赖安装失败，请检查网络连接或手动安装"
    exit 1
fi
