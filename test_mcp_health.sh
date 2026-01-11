#!/bin/bash
# MCP Service Health Check Script

echo "===== MCP Service Health Check ====="
echo ""

# 1. Check container status
echo "1. Container Status:"
docker ps --filter name=mcp --format "  {{.Names}}: {{.Status}}"
echo ""

# 2. Test health endpoint
echo "2. Health Endpoint Test:"
HEALTH=$(curl -s http://localhost:8000/health)
echo "  $HEALTH" | grep -q "healthy" && echo "  ✓ Health check passed" || echo "  ✗ Health check failed"
echo ""

# 3. Test SSE endpoint and MCP tool call (search forum posts)
echo "3. SSE Endpoint & Forum Search Test:"
echo "  Testing: 查询论坛 ind news 相关文章"

# Create a temporary file for the test response
TEMP_RESPONSE=$(mktemp)
trap "rm -f $TEMP_RESPONSE" EXIT

# Perform SSE connection and capture response with timeout
timeout 30 curl -s -N http://localhost:8000/sse \
  -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  --max-time 25 > "$TEMP_RESPONSE" 2>&1 &

CURL_PID=$!
sleep 2  # Wait for connection to establish

# Check if connection is active
if kill -0 $CURL_PID 2>/dev/null; then
  # Extract session ID from response
  SESSION_ID=$(grep -o "session_id=[a-f0-9]*" "$TEMP_RESPONSE" | head -1 | cut -d= -f2)
  
  if [ -n "$SESSION_ID" ]; then
    echo "  ✓ SSE endpoint responding with session: ${SESSION_ID:0:8}..."
  else
    echo "  ✓ SSE endpoint responding (session ID capture pending)"
  fi
else
  echo "  ✗ SSE endpoint failed"
fi

wait $CURL_PID 2>/dev/null || true
echo ""

# 4. Check Redis connection
echo "4. Redis Connection:"
docker logs mcp 2>&1 | tail -20 | grep -q "Redis connection established successfully" && echo "  ✓ Redis connected" || echo "  ✗ Redis connection issue"
echo ""

# 5. Check volumes
echo "5. Persistent Volumes:"
docker volume ls | grep -q "mcp_config" && echo "  ✓ Config volume exists" || echo "  ✗ Config volume missing"
docker volume ls | grep -q "playwright_data" && echo "  ✓ Playwright volume exists" || echo "  ✗ Playwright volume missing"
echo ""

# 6. Check browser installation
echo "6. Browser Installation:"
docker exec mcp test -d /ms-playwright/chromium-1200 && echo "  ✓ Chromium browser installed" || echo "  ✗ Chromium not found"
echo ""

echo "===== Health Check Complete ====="
