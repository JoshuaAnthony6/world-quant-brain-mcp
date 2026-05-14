# world-quant-brain-mcp

WorldQuant BRAIN 平台的 MCP（Model Context Protocol）服务端，通过 Streamable HTTP 暴露工具，可在 Claude Code、Codex 等支持 MCP 的客户端中直接调用。

- 默认监听：`http://localhost:8876/mcp`（Docker 默认对外映射为 `8876`）
- 传输协议：Streamable HTTP
- 依赖服务：Redis（用于缓存与并发锁）

---

## 目录

- [一、Docker 安装（推荐）](#一docker-安装推荐)
- [二、Python 安装（Ubuntu / Windows）](#二python-安装ubuntu--windows)
- [三、环境变量配置](#三环境变量配置)
- [四、在 Claude Code 中配置使用](#四在-claude-code-中配置使用)
- [五、在 Codex 中配置使用](#五在-codex-中配置使用)
- [六、生产部署（可选）](#六生产部署可选)

---

## 一、Docker 安装（推荐）

Docker 方式会一并启动 MCP 服务和 Redis，无需在宿主机安装 Python、Playwright 浏览器以及系统依赖。

### 1. 前置准备

- 安装 [Docker](https://docs.docker.com/get-docker/) 与 Docker Compose（Windows 直接安装 Docker Desktop；Ubuntu 安装 `docker-ce` 与 `docker-compose-plugin`）。
- 克隆本仓库：

```bash
git clone https://github.com/lavender1203/world-quant-brain-mcp.git 
cd world-quant-brain-mcp
```

### 2. 准备 `.env`

```bash
cp .env.example .env
```

编辑 `.env`，至少填入：

```dotenv
CREDENTIALS_EMAIL="你的BRAIN账号"
CREDENTIALS_PASSWORD="你的BRAIN密码"
```

其余可选配置见 [环境变量配置](#三环境变量配置)。

### 3. 启动

```bash
docker compose up -d --build
```

- MCP 服务地址：`http://localhost:8876/mcp`
- Redis：`localhost:6479`

查看日志 / 状态：

```bash
docker compose logs -f mcp
docker compose ps
```

停止 / 重启：

```bash
docker compose down
docker compose restart mcp
```

> Windows 用户在 PowerShell / WSL2 终端中执行相同命令即可。建议使用 WSL2 后端的 Docker Desktop。

---

## 二、Python 安装（Ubuntu / Windows）

如不使用 Docker，可直接在本机用 Python 运行。需要本机额外提供 Redis（可用 `docker run -p 6379:6379 redis:6-alpine` 单独启动）。

### Ubuntu

1. 安装系统依赖（Playwright 启动 Chromium 需要）：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git \
    libnspr4 libnss3 libgbm1 libgtk-3-0 libx11-xcb1 libxss1 \
    libatk1.0-0 libatk-bridge2.0-0 libpango-1.0-0 libxrandr2 \
    libxcomposite1 libxdamage1 libxkbcommon0 libcups2 ca-certificates \
    fonts-liberation xz-utils unzip wget
sudo apt-get install -y libasound2 || sudo apt-get install -y libasound2t64 || true
```

2. 创建虚拟环境并安装依赖：

```bash
git clone https://github.com/lavender1203/world-quant-brain-mcp.git 
cd world-quant-brain-mcp

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
python -m playwright install chromium
```

3. 配置 `.env`（同 Docker 部分）：

```bash
cp .env.example .env
# 编辑 CREDENTIALS_EMAIL / CREDENTIALS_PASSWORD
```

4. 启动服务：

```bash
python main.py
```

默认监听 `http://0.0.0.0:8000/mcp`。

### Windows

1. 安装 [Python 3.12+](https://www.python.org/downloads/windows/)（安装时勾选 *Add Python to PATH*）与 [Git for Windows](https://git-scm.com/download/win)。
2. 在 PowerShell 中执行：

```powershell
git clone https://github.com/lavender1203/world-quant-brain-mcp.git 
cd world-quant-brain-mcp

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
python -m playwright install chromium
```

> 如 PowerShell 提示执行策略限制，先运行：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

3. 复制并编辑配置：

```powershell
Copy-Item .env.example .env
notepad .env
```

4. 启动 Redis（任选其一）：

```powershell
# 方式 A：用 Docker Desktop 临时跑一个 Redis
docker run -d --name mcp-redis -p 6379:6379 redis:6-alpine

# 方式 B：使用 Memurai / WSL2 中的 redis-server
```

5. 启动 MCP：

```powershell
python main.py
```

---

## 三、环境变量配置

所有配置通过 `.env` 注入（亦兼容 `config/user_config.json`，但 `.env` 优先级更高）。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `CREDENTIALS_EMAIL` / `CREDENTIALS_PASSWORD` | ✅ | BRAIN 账号密码 |
| `MCP_HOST` | | 监听地址，默认 `0.0.0.0` |
| `MCP_PORT` | | 监听端口，默认 `8000` |
| `MCP_STREAMABLE_HTTP_PATH` | | MCP 路径，默认 `/mcp` |
| `API_SETTINGS_TIMEOUT` | | API 超时（秒），默认 `30` |
| `FORUM_SETTINGS_BASE_URL` | | 论坛 URL，默认 `https://support.worldquantbrain.com` |
| `FORUM_SETTINGS_HEADLESS` | | Playwright headless，默认 `true` |
| `FORUM_SETTINGS_TIMEOUT` | | 论坛超时，默认 `15` |
| `FORUM_MAX_CONCURRENCY` | | 论坛并发，默认 `1` |
| `FORUM_RATE_LIMIT_SECONDS` | | 论坛调用间隔，默认 `0` |
| `REDIS_HOST` / `REDIS_PORT` | | Redis 地址，Docker 模式自动为 `redis:6379` |

完整字段参考 `.env.example`。

---

## 四、在 Claude Code 中配置使用

Claude Code 通过 HTTP transport 接入本服务。

### 方式 A：命令行添加

```bash
# 用户级（所有项目可见）
claude mcp add --transport http brain http://localhost:8876/mcp --scope user

# 或项目级（仅当前项目，会写入 .mcp.json）
claude mcp add --transport http brain http://localhost:8876/mcp --scope project
```

Python 直跑模式将 `8876` 改成 `8000` 即可。

### 方式 B：手动写入配置

项目级配置文件 `.mcp.json`（放在仓库根目录）：

```json
{
  "mcpServers": {
    "brain": {
      "type": "http",
      "url": "http://localhost:8876/mcp"
    }
  }
}
```

或用户级 `~/.claude.json`（`mcpServers` 同上结构）。

### 验证

```bash
claude mcp list
```

在 Claude Code 会话中输入 `/mcp` 应能看到 `brain` 已连接，工具列表中会出现 BRAIN 相关工具（创建模拟、查询数据集、论坛操作等）。

---

## 五、在 Codex 中配置使用

Codex 通过 `~/.codex/config.toml` 接入 MCP。Streamable HTTP 服务需启用 rmcp 客户端：

```toml
experimental_use_rmcp_client = true

[mcp_servers.brain]
url = "http://localhost:8876/mcp"
```

- Ubuntu / macOS：`~/.codex/config.toml`
- Windows：`%USERPROFILE%\.codex\config.toml`

保存后重启 Codex CLI / IDE 扩展。进入会话后查看 MCP 状态应能看到 `brain` 已连接。

---

## 六、生产部署（可选）

### Nginx 反向代理 + HTTPS

仓库提供 `deploy/nginx/mcp_http.conf` 示例：

```bash
sudo cp deploy/nginx/mcp_http.conf /etc/nginx/sites-available/mcp_http
sudo ln -s /etc/nginx/sites-available/mcp_http /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

关键点：

- `proxy_http_version 1.1` + `proxy_set_header Connection ""` 保持长连接
- `proxy_buffering off` 流式响应
- `proxy_read_timeout 3600` 容忍 BRAIN 长耗时调用
- 用 certbot / Caddy 在前面接入 TLS

### systemd 守护

```bash
sudo cp deploy/systemd/mcp-http.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-http.service
sudo journalctl -u mcp-http -f
```

该 unit 默认通过 `docker compose` 启停，使用仓库内的 `.env` 作为环境文件，按需修改 `WorkingDirectory` / `User`。
