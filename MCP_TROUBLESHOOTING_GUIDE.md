# MCP 配置和论坛搜索诊断指南

## 问题诊断

你在 VS Code Insiders 中输入 "查询论坛 ind news 相关文章" 时卡住，这可能是由以下几个原因造成的：

### 1. **配置正确性检查** ✅
你的 `mcp.json` 配置是正确的：
```json
{
  "servers": {
    "wqb-mcp-server": {
      "url": "http://localhost:8000/sse",
      "type": "http"
    }
  },
  "inputs": []
}
```

### 2. **服务状态检查** ✅
所有关键组件都正常运行：
- **SSE 端点**: ✓ 正常响应
- **健康检查**: ✓ 返回健康状态
- **Redis 连接**: ✓ 已建立
- **浏览器**: ✓ 已预安装

### 3. **可能的卡顿原因**

#### 原因 A：平台认证信息缺失
```
症状: 搜索功能无响应，30秒后超时
原因: 缺少 BRAIN 平台认证
解决方案:
1. 在 Docker 容器中创建配置文件
2. 提供有效的 BRAIN 账户凭证
```

#### 原因 B：网络连接超时
```
症状: 连接建立但响应缓慢或无响应
原因: 可能是 SSE 流处理缓慢
解决方案:
1. 检查网络连接
2. 增加客户端超时时间
3. 重启 MCP 服务
```

#### 原因 C：浏览器初始化延迟
```
症状: 首次搜索特别慢（30秒以上）
原因: Playwright 浏览器第一次使用需要初始化
解决方案:
1. 等待首次初始化完成
2. 第二次搜索会明显加快
3. 可以预先运行测试来预热系统
```

## 快速诊断步骤

### 步骤 1：验证服务状态
```bash
# 在容器主机上运行
curl http://localhost:8000/health | jq .
```

预期输出：
```json
{
  "status": "healthy",
  "service": "brain-platform-mcp",
  "timestamp": "2026-01-11T...",
  "redis_connected": true
}
```

### 步骤 2：测试 SSE 连接
```bash
# 应该立即返回 event 和 session_id
curl -s -N http://localhost:8000/sse -H "Accept: text/event-stream" --max-time 3 | head -1
```

预期输出：
```
event: endpoint
data: /sse/messages/?session_id=...
```

### 步骤 3：运行综合测试
```bash
# 完整的诊断测试
python3 test_mcp_forum_search.py
```

## VS Code Insiders 使用指南

### 配置方案

**方案 1：本地 Docker 环境（推荐）**
```json
{
  "servers": {
    "wqb-mcp-server": {
      "url": "http://localhost:8000/sse",
      "type": "http"
    }
  },
  "inputs": []
}
```

**方案 2：远程 Docker 环境**
```json
{
  "servers": {
    "wqb-mcp-server": {
      "url": "http://<your-server-ip>:8000/sse",
      "type": "http"
    }
  },
  "inputs": []
}
```

### 使用步骤

1. **启动 MCP 服务**
   ```bash
   cd /opt/project/world-quant-brain-mcp
   docker compose up -d
   ```

2. **验证服务就绪**
   ```bash
   python3 test_mcp_forum_search.py
   ```

3. **在 VS Code 中配置**
   - 打开 `Insiders` 设置
   - 找到 MCP 配置部分
   - 输入上述 `mcp.json` 内容
   - 保存并重新加载

4. **发送提示词**
   ```
   查询论坛 ind news 相关文章
   ```

5. **预期流程**
   - MCP 客户端连接到 SSE 端点
   - 建立 WebSocket 会话
   - 发送 `search_forum_posts` 工具调用
   - 搜索包含 "ind" 和 "news" 的帖子
   - 返回相关文章列表（可能包括）：
     - 印度市场新闻
     - 行业新闻
     - 经济新闻
     - 科技新闻
     - 相关研究报告

## 故障排查清单

| 问题 | 检查项 | 解决方案 |
|------|--------|--------|
| 连接失败 | `curl http://localhost:8000/health` | 重启容器: `docker compose restart mcp` |
| SSE 无响应 | `curl -N http://localhost:8000/sse ...` | 检查防火墙规则 |
| 搜索超时 | 查看日志: `docker logs mcp` | 增加超时时间，检查认证 |
| 浏览器错误 | 查看浏览器日志 | 重新构建: `docker compose build --no-cache` |
| Redis 连接失败 | `docker exec mcp redis-cli ping` | 重启 Redis: `docker compose restart redis` |

## 性能优化建议

### 1. 预热系统
在首次使用论坛搜索前，运行：
```bash
python3 test_mcp_forum_search.py
```

### 2. 增加超时时间
在 VS Code 中调整 MCP 客户端超时（通常在设置中）：
```json
{
  "mcp": {
    "timeout": 60000  // 60 秒
  }
}
```

### 3. 启用缓存
MCP 服务器已内置 Redis 缓存，搜索结果会被缓存 24 小时。

### 4. 监控日志
```bash
# 实时监视日志
docker logs mcp -f --tail 20
```

## 常见搜索查询示例

```
查询论坛 ind news 相关文章
→ 搜索: 印度 + 新闻

查询论坛 alpha 相关文章
→ 搜索: alpha (量化策略)

查询论坛 simulation 相关文章  
→ 搜索: 模拟 (回测)

查询论坛 strategy 相关文章
→ 搜索: 策略 (交易策略)
```

## 调试模式

如需更详细的日志输出，修改环境变量：

```bash
# 编辑 docker-compose.yml
environment:
  - PYTHONUNBUFFERED=1
  - LOG_LEVEL=DEBUG  # 添加此行
  - REDIS_HOST=redis
  - REDIS_PORT=6379
```

然后重启：
```bash
docker compose up -d
```

## 总结

✅ **你的配置是正确的**
✅ **MCP 服务运行正常**
✅ **所有依赖都已就位**

如果仍然出现卡顿，可能是：
1. **首次运行** - 浏览器初始化可能需要 30 秒
2. **认证缺失** - 需要配置 BRAIN 账户
3. **网络延迟** - 检查互联网连接

**下一步**: 在 VS Code Insiders 中尝试发送提示词。如果仍有问题，运行诊断脚本并检查日志。
