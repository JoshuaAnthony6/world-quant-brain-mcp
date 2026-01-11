#!/bin/bash
# Quick Diagnosis Script for MCP Forum Search Hanging Issue
# 快速诊断脚本：解决 MCP 论坛搜索卡顿问题

echo "===== MCP Forum Search Hanging Issue Diagnosis ====="
echo ""
echo "诊断时间: $(date)"
echo ""

# Check 1: Service connectivity
echo "📡 1. 服务连接检查"
echo "===================="

if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "✓ 健康检查端点: 正常"
else
    echo "✗ 健康检查端点: 失败"
    echo "  → 解决方案: 运行 'docker compose restart mcp'"
fi

if curl -s -m 3 -N http://localhost:8000/sse -H "Accept: text/event-stream" 2>&1 | grep -q "session_id"; then
    echo "✓ SSE 端点: 正常"
else
    echo "✗ SSE 端点: 失败或超时"
    echo "  → 解决方案: 检查防火墙，运行 'docker logs mcp'"
fi

echo ""

# Check 2: Container resources
echo "💾 2. 容器资源检查"
echo "=================="

CONTAINER_STATUS=$(docker ps --filter name=mcp --format "{{.Status}}" | head -1)
echo "✓ 容器状态: $CONTAINER_STATUS"

MEMORY=$(docker inspect mcp --format='{{.State.Pid}}' 2>/dev/null)
if [ ! -z "$MEMORY" ]; then
    echo "✓ 容器进程: 运行中"
else
    echo "✗ 容器进程: 未运行"
    echo "  → 解决方案: 运行 'docker compose up -d'"
fi

echo ""

# Check 3: Database/Cache connectivity
echo "🔄 3. 数据库和缓存检查"
echo "======================="

if docker exec mcp-redis redis-cli ping > /dev/null 2>&1; then
    echo "✓ Redis 连接: 正常"
else
    echo "✗ Redis 连接: 失败"
    echo "  → 解决方案: 运行 'docker compose restart mcp-redis'"
fi

echo ""

# Check 4: Authentication setup
echo "🔐 4. 认证配置检查"
echo "==================="

if docker exec mcp test -f /app/config/user_config.json > /dev/null 2>&1; then
    echo "✓ 配置文件: 存在"
    HAS_CREDS=$(docker exec mcp grep -c '"email"' /app/config/user_config.json 2>/dev/null || echo "0")
    if [ "$HAS_CREDS" != "0" ]; then
        echo "✓ 认证信息: 已配置"
    else
        echo "⚠ 认证信息: 未完全配置"
        echo "  → 建议: 可能导致搜索功能不完整"
    fi
else
    echo "⚠ 配置文件: 尚未创建（首次使用）"
    echo "  → 备注: 认证信息会在首次访问时创建"
fi

echo ""

# Check 5: Performance metrics
echo "⚡ 5. 性能指标"
echo "==============="

# Check container uptime
CREATED=$(docker inspect mcp --format='{{.Created}}' 2>/dev/null | cut -d'T' -f2 | cut -d'.' -f1)
echo "✓ 容器启动时间: $CREATED"

# Check disk usage of volume
VOLUME_SIZE=$(docker volume inspect world-quant-brain-mcp_mcp_config --format='{{.Mountpoint}}' 2>/dev/null)
if [ ! -z "$VOLUME_SIZE" ]; then
    echo "✓ 配置卷: 已挂载"
fi

echo ""

# Check 6: Browser availability
echo "🌐 6. 浏览器可用性"
echo "==================="

if docker exec mcp test -d /ms-playwright/chromium-1200 > /dev/null 2>&1; then
    echo "✓ Chromium 浏览器: 已安装"
else
    echo "⚠ Chromium 浏览器: 需要重新构建"
    echo "  → 解决方案: 运行 'docker compose build --no-cache'"
fi

echo ""

# Check 7: Network configuration
echo "🔗 7. 网络配置"
echo "==============="

PORT_CHECK=$(netstat -tlnp 2>/dev/null | grep -c ":8000" || ss -tlnp 2>/dev/null | grep -c ":8000" || echo "0")
if [ "$PORT_CHECK" != "0" ]; then
    echo "✓ 端口 8000: 已监听"
else
    echo "⚠ 端口 8000: 未监听"
    echo "  → 解决方案: 检查 docker ps"
fi

echo ""

# Check 8: Recent errors in logs
echo "📋 8. 近期日志分析"
echo "==================="

ERROR_COUNT=$(docker logs mcp --since 10m 2>&1 | grep -iE "error|exception|timeout" | wc -l)
if [ "$ERROR_COUNT" -eq 0 ]; then
    echo "✓ 近期错误: 无"
else
    echo "⚠ 近期错误: 发现 $ERROR_COUNT 条"
    echo "  最后的错误:"
    docker logs mcp --since 10m 2>&1 | grep -iE "error|exception|timeout" | tail -3 | sed 's/^/    /'
fi

echo ""
echo "===== 诊断完成 ====="
echo ""

# Recommendations
echo "🔧 建议处理步骤:"
echo "1. 如果 SSE 端点异常，运行: docker logs mcp | tail -50"
echo "2. 如果缓存问题，运行: docker compose restart mcp-redis"
echo "3. 如果浏览器问题，运行: docker compose build --no-cache"
echo "4. 如果认证问题，在 VS Code 中重新配置凭证"
echo "5. 运行完整测试: python3 test_mcp_forum_search.py"
echo ""
echo "📖 详细说明: 查看 MCP_TROUBLESHOOTING_GUIDE.md"
echo ""
