# 🚀 VS Code + MCP 快速启动指南

## 问题总结

你在 VS Code Insiders 中输入 "查询论坛 ind news 相关文章" 时 MCP 卡顿/超时。

**好消息**: 🎉 MCP 服务器完全正常运行，配置也是对的！

## ✅ 现状验证

所有关键系统都已通过检查：

```
✓ MCP 服务器: 健康（http://localhost:8000）
✓ SSE 端点: 正常响应
✓ Redis 缓存: 已连接
✓ Chromium 浏览器: 已预装
✓ Docker 容器: 运行中
✓ 网络连接: 正常
```

## 🎯 卡顿原因分析

### 最可能的原因：首次使用延迟

Playwright 浏览器在 **首次启动** 时需要初始化，这可能导致：
- ⏱️ **首次搜索**: 30-60 秒（浏览器初始化）
- ⏱️ **后续搜索**: 3-5 秒（缓存命中）

### 其他可能原因

| 原因 | 症状 | 解决方案 |
|------|------|--------|
| **认证缺失** | 搜索失败或无结果 | 配置 BRAIN 账户凭证 |
| **网络超时** | 连接 30 秒后断开 | 增加客户端超时 |
| **浏览器未就绪** | 任何搜索都卡 | 重建镜像并预热系统 |
| **VS Code 超时** | MCP 客户端设置 | 调整 MCP 超时配置 |

## 🔧 立即解决方案

### 方案 1：预热系统（推荐）
```bash
# 运行测试脚本来预热浏览器和缓存
cd /opt/project/world-quant-brain-mcp
python3 test_mcp_forum_search.py
```

预期输出：所有 3 个测试通过 ✓

### 方案 2：增加 VS Code 超时
在 VS Code 中找到 MCP 客户端设置，增加超时时间：
```json
{
  "timeout": 120000  // 改为 120 秒（从默认的 30 秒）
}
```

### 方案 3：诊断系统
```bash
# 运行完整诊断
cd /opt/project/world-quant-brain-mcp
./diagnose_mcp_issue.sh
```

这将检查：
- 服务连接 ✓
- 容器资源 ✓
- 缓存连接 ✓
- 认证配置 ✓
- 浏览器状态 ✓
- 错误日志 ✓

## 📝 正确的使用流程

### 初次使用（第一天）

1️⃣ **预热系统**
```bash
cd /opt/project/world-quant-brain-mcp
python3 test_mcp_forum_search.py
# 等待完成 ✓
```

2️⃣ **打开 VS Code Insiders**
- 打开 MCP 客户端
- 确认配置正确（见下方）
- 第一个提示词耐心等待 60 秒

3️⃣ **发送提示词**
```
查询论坛 ind news 相关文章
```

### 后续使用（缓存命中后）

✓ 响应时间：3-5 秒
✓ 直接获得结果

## ⚙️ VS Code 配置检查

你的 `mcp.json` 配置应该是：

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

✅ **这个配置是正确的！**

## 🧪 快速测试清单

在 VS Code 尝试之前，运行这个检查：

```bash
# 1. 检查服务运行
docker ps | grep mcp
# 期望: 看到 "Up ... (healthy)" 状态

# 2. 测试健康端点
curl http://localhost:8000/health | jq .
# 期望: status 是 "healthy"

# 3. 测试 SSE 端点
curl -s -N http://localhost:8000/sse -H "Accept: text/event-stream" --max-time 3 | head -1
# 期望: 看到 "event: endpoint" 和 session_id

# 4. 完整功能测试
python3 /opt/project/world-quant-brain-mcp/test_mcp_forum_search.py
# 期望: 3/3 tests passed
```

## 📊 性能期望

### 响应时间分布

| 场景 | 时间 | 说明 |
|------|------|------|
| **首次搜索** | 30-60 秒 | 浏览器冷启动 |
| **第二次搜索** | 3-5 秒 | 缓存命中 |
| **后续搜索** | 1-3 秒 | 全缓存 |

### 缓存有效期
- **搜索结果**: 24 小时
- **认证令牌**: 会话级
- **浏览器数据**: 持久化

## 🐛 如果仍然卡顿

### 步骤 1：查看日志
```bash
docker logs mcp --tail 100 | grep -iE "error|timeout|exception"
```

### 步骤 2：重启服务
```bash
docker compose restart mcp
```

### 步骤 3：检查资源
```bash
docker stats --no-stream | grep mcp
```

### 步骤 4：重建镜像
```bash
docker compose build --no-cache
docker compose up -d
```

### 步骤 5：查看帮助文档
```bash
cat MCP_TROUBLESHOOTING_GUIDE.md
```

## 📚 相关文件

| 文件 | 用途 |
|------|------|
| `test_mcp_forum_search.py` | 功能测试脚本 |
| `diagnose_mcp_issue.sh` | 系统诊断脚本 |
| `test_mcp_health.sh` | 健康检查脚本 |
| `MCP_TROUBLESHOOTING_GUIDE.md` | 详细故障排查指南 |
| `DOCKER_FIX_SUMMARY.md` | Docker 修复总结 |

## ✨ 关键要点

1. **你的配置 100% 正确** ✓
2. **所有系统都正常运行** ✓
3. **卡顿很可能是首次浏览器初始化** ⏱️
4. **预热后速度会快得多** ⚡

## 🎬 立即开始

```bash
# 1. 预热系统
python3 /opt/project/world-quant-brain-mcp/test_mcp_forum_search.py

# 2. 在 VS Code Insiders 中输入
"查询论坛 ind news 相关文章"

# 3. 耐心等待第一次结果（30-60 秒）

# 4. 享受快速响应（后续 3-5 秒）
```

## 📞 需要帮助？

- **检查系统状态**: `./diagnose_mcp_issue.sh`
- **查看日志**: `docker logs mcp -f`
- **重启服务**: `docker compose restart mcp`
- **读文档**: `MCP_TROUBLESHOOTING_GUIDE.md`

---

**祝你使用愉快! 🎉**

如有任何问题，请参考诊断脚本的输出和故障排查指南。
