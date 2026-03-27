@echo off
chcp 65001 >nul
echo ========================================
echo R2 文件上传工具 - 依赖安装脚本
echo ========================================
echo.

echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未找到 Python，请先安装 Python 3.8 或更高版本
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo ✅ Python 环境检查通过
echo.

echo [2/4] 检查 pip...
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: pip 未安装
    pause
    exit /b 1
)
echo ✅ pip 可用
echo.

echo [3/4] 升级 pip 到最新版本...
python -m pip install --upgrade pip
echo.

echo [4/4] 安装依赖包...
python -m pip install -r requirements.txt
echo.

if errorlevel 1 (
    echo ❌ 依赖安装失败，请检查网络连接或手动安装
    pause
    exit /b 1
)

echo ========================================
echo ✅ 所有依赖安装完成！
echo ========================================
echo.
echo 现在可以运行程序了:
echo     python r2_uploader_gui.py
echo.
pause
