# 公开模组库

模组库位于 [AutoKeeper 的 mod_library 分支](https://github.com/satoshinji2992/autokeeper/tree/mod_library)，与联机服务器分开。模组以原文件存放，可逐级浏览规则、时代、模组、人物、地点和线索；主持资料注明剧透。下载不需要密码。

## 安装

游戏主界面选择「导入模组 → 模组库」。选择模组查看简介、人数和时长，再下载；已有的同名模组会先要求确认替换。

命令行：

```sh
autokeeper library list
autokeeper library get shiling
autokeeper library get shiling --replace
```

客户端只下载所选模组的文件，不克隆整库。每个目录固定到一个 Git 提交，并校验签名、文件大小与 SHA-256。断线后重试会复用已校验文件；安装失败时恢复原模组。SOCKS 代理支持随程序安装，无需另外补装 socksio。

## 投稿

[投稿说明](https://github.com/satoshinji2992/autokeeper/blob/mod_library/CONTRIBUTING.md)给出了目录结构、校验命令和 PR 流程。向 `mod_library` 分支提交可读原文件即可，不需要上传压缩包、密码或签名密钥。维护者审核并发布目录后，客户端才能安装新的内容。

原作者、来源、许可和素材署名随模组保留。公开浏览不改变模组原有授权范围。

## 自定义来源

需要使用自己维护的目录时，可用 `autokeeper library list --server <目录地址>`，或设置 `AUTOKEEPER_LIBRARY`。目录仍必须由客户端信任的发布者签名；更换下载地址不会改变信任规则。
