# 本机公网访问

游戏和存档保留在本机，通过 Cloudflare Quick Tunnel 提供临时 HTTPS 地址；不需要域名或服务器。公网入口有独立口令，登录后才能打开游戏和 WebSocket。模型使用现有的 DeepSeek 配置与密钥，不复制或修改游戏设置。

当前工作区已安装并校验 `saves/public-share/bin/cloudflared`。运行：

```bash
uv run python deploy/share.py start
uv run python deploy/share.py stop
```

启动结果包含访问地址；口令在 `saves/public-share/credentials.json`，进程信息和地址在同目录的 `status.json`，日志为 `gateway.log` 和 `tunnel.log`。这些运行文件均位于 Git 忽略的 `saves/`。重复启动会复用正在运行的入口，停止命令只终止本工具创建且身份匹配的进程组。

本机服务只监听 `127.0.0.1:8090`。口令登录生成限时、HttpOnly、Secure、SameSite Cookie；WebSocket 校验登录与来源，最多打开两个游戏窗口。入口不提供代码目录、存档或密钥文件下载。

Quick Tunnel 的地址在重建后会变化，电脑或隧道关闭就无法访问；它适合个人试用，不是永久托管。手机建议横屏。新电脑需要先从 Cloudflare 官方发布页安装对应架构的 `cloudflared` 到上述路径，并校验官方发布的 SHA-256。

验证：

```bash
uv run pytest tests/test_public_gateway.py -q
```

参考：[Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)、[WebSocket 支持](https://developers.cloudflare.com/network/websockets/)。
