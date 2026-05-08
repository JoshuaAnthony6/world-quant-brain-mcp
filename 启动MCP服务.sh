#!/bin/bash

echo "========================================"
echo "WorldQuant BRAIN MCP Server (Python)"
echo "========================================"
echo ""

check_python() {
    if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
        echo "[错误] 未找到Python，请先安装Python 3.8+"
        exit 1
    fi
    
    PYTHON_CMD="python3"
    if command -v python &> /dev/null; then
        PYTHON_CMD="python"
    fi
}

check_dependencies() {
    echo "[1/3] 检查依赖..."
    if ! $PYTHON_CMD -c "import playwright" &> /dev/null; then
        echo "[警告] 依赖未安装，正在安装..."
        $PYTHON_CMD -m pip install -r requirements.txt
        echo "[信息] 安装Playwright浏览器..."
        $PYTHON_CMD -m playwright install chromium
        $PYTHON_CMD -m playwright install-deps chromium
    fi
}

check_env() {
    echo "[2/3] 检查配置文件..."
    if [ ! -f ".env" ]; then
        echo "[警告] .env文件不存在，正在从示例创建..."
        cp .env.example .env
        echo "[警告] 请编辑 .env 文件填入您的BRAIN凭证！"
        echo ""
        echo "请运行以下命令编辑配置:"
        echo "  nano .env"
        echo ""
        read -p "按回车键继续(配置将在下次启动时生效)..."
    fi
}

check_redis() {
    echo "[3/3] 检查Redis连接..."
    if $PYTHON_CMD -c "import redis; r=redis.Redis(host='localhost', port=6379, socket_connect_timeout=1); r.ping()" 2> /dev/null; then
        echo "[OK] Redis连接成功"
    else
        echo "[信息] Redis未运行或无法连接，缓存功能将不可用（不影响核心功能）"
        echo "[信息] 如需Redis，请运行: redis-server"
    fi
}

start_server() {
    echo ""
    echo "========================================"
    echo "启动MCP Server..."
    echo "========================================"
    echo ""
    echo "服务将在 http://localhost:8000/mcp 运行"
    echo "按Ctrl+C停止服务"
    echo ""
    
    export MCP_HOST=127.0.0.1
    export MCP_PORT=8000
    
    $PYTHON_CMD main.py
}

check_python
check_dependencies
check_env
check_redis
start_server
