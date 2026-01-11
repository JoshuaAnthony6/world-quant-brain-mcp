容器化运行说明

快速开始：

推荐使用 `docker compose` 来管理服务；仓库内提供 `Makefile` 以便快速操作。

1. 使用 Makefile 启动（推荐）

```bash
make up-detach    # 构建并后台启动服务
make logs         # 查看实时日志
make ps           # 列出服务状态
```

2. 直接使用 Docker Compose

```bash
docker compose up --build -d
docker compose logs -f
```

注意事项：
- 镜像基于 Playwright 官方镜像，已包含浏览器二进制和运行依赖，避免在宿主机手动安装大量系统库。
- 默认将仓库挂载到容器 `/app`，容器启动命令为 `python main.py`；如果你的项目入口不同，请修改 `Dockerfile` 的 `CMD` 或在 `docker-compose.yml` 中覆盖 `command`。
- 如果需要在生产环境中使用 systemd 来管理容器，可以创建一个 unit，使用 `docker compose -f /path/to/docker-compose.yml up -d`。

注意：此项目现在推荐并仅支持通过容器运行，已移除对直接 systemd 启动单元的内建支持。请使用 `docker compose`、`make`（上文）或 `docker run` 管理容器生命周期。
