# 开发工具

这里放开发时用的脚本，它们的运行文件和其他开发输出（评测报告、自动跑团记录）都写进 Git 忽略的 `dev/output/`。

## 本机公网访问

游戏和存档保留在本机，通过 Cloudflare Quick Tunnel 提供临时 HTTPS 地址；不需要域名或服务器。公网入口有独立口令，登录后才能打开游戏和 WebSocket。模型使用现有的 DeepSeek 配置与密钥，不复制或修改游戏设置。

`cloudflared` 放在 `dev/output/public-share/bin/cloudflared`（以前的版本放在 `saves/public-share/`，整个文件夹挪过来即可）。运行：

```bash
uv run python dev/share.py start
uv run python dev/share.py stop
```

启动结果包含访问地址；口令在 `dev/output/public-share/credentials.json`，进程信息和地址在同目录的 `status.json`，日志为 `gateway.log` 和 `tunnel.log`。这些运行文件均位于 Git 忽略的 `dev/output/`。重复启动会复用正在运行的入口，停止命令只终止本工具创建且身份匹配的进程组。

本机服务只监听 `127.0.0.1:8090`。口令登录生成限时、HttpOnly、Secure、SameSite Cookie；WebSocket 校验登录与来源，最多打开两个游戏窗口。入口不提供代码目录、存档或密钥文件下载。

Quick Tunnel 的地址在重建后会变化，电脑或隧道关闭就无法访问；它适合个人试用，不是永久托管。手机建议横屏。新电脑需要先从 Cloudflare 官方发布页安装对应架构的 `cloudflared` 到上述路径，并校验官方发布的 SHA-256。

验证：

```bash
uv run pytest tests/test_public_gateway.py -q
```

参考：[Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)、[WebSocket 支持](https://developers.cloudflare.com/network/websockets/)。

## 项目展示页

GitHub Pages 的源码在 `docs/index.html` 和 `docs/site/`，直接复用 `docs/img/` 的实机图。网页用 HTML/CSS 延续 Textual 的琥珀色字符面板，复用游戏的 ASCII 标题字，叠加静态 SVG 颗粒与扫描线纹理。纯静态页面，无须安装前端依赖。本地预览：

```bash
python -m http.server 8080 --bind 127.0.0.1 --directory docs
```

打开 `http://127.0.0.1:8080/`。页面的链接和资源均按项目子目录编写，部署到 `/autokeeper/` 时也能正常使用。修改图片后，更新 `docs/site/main.js` 中图集的尺寸和说明；正文、模型启动示例与 README 保持一致。

发布地址为 <https://satoshinji2992.github.io/autokeeper/>。仓库 Pages 使用 `main` 分支的 `/docs`，提交并推送网页改动后会自动发布，`docs/.nojekyll` 用于直接发布静态文件。运行中的游戏、API 密钥和存档仍留在玩家本机。
