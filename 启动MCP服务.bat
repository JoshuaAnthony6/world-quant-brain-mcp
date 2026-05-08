@echo off
chcp 65001 >nul
title BRAIN MCP Server

echo ========================================
echo WorldQuant BRAIN MCP Server (Python)
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 检查依赖是否已安装
echo [1/3] 检查依赖...
pip show playwright >nul 2>&1
if errorlevel 1 (
    echo [警告] 依赖未安装，正在安装...
    pip install -r requirements.txt
    echo [信息] 安装Playwright浏览器...
    playwright install chromium
    playwright install-deps chromium
)

REM 检查.env文件
if not exist .env (
    echo [警告] .env文件不存在，正在从示例创建...
    copy .env.example .env
    echo [警告] 请编辑 .env 文件填入您的BRAIN凭证！
    notepad .env
)

REM 检查Redis (可选)
echo [2/3] 检查Redis连接...
python -c "import redis; r=redis.Redis(host='localhost', port=6379, socket_connect_timeout=1); r.ping(); print('[OK] Redis连接成功')" 2>nul
if errorlevel 1 (
    echo [信息] Redis未运行或无法连接，缓存功能将不可用（不影响核心功能）
    echo [信息] 如需Redis，请运行: redis-server
)

REM 启动服务
echo [3/3] 启动MCP Server...
echo.
echo 服务将在 http://localhost:8000/mcp 运行
echo 按Ctrl+C停止服务
echo.

REM 设置环境变量
set MCP_HOST=127.0.0.1
set MCP_PORT=8000

python main.py

pause
