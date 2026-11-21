# 投稿与维护

向本仓库的 `mod_library` 分支提交 PR。模组和素材直接作为普通文件存放，不上传压缩包，不使用 Git LFS。

## 目录

```text
modules/<id>/
  module/
    module.yaml
    places.yaml
    people.yaml
    clues.yaml
    story.yaml
    situation.yaml
    art/
    plugins/          # 可选规则扩展
    source/           # 有权公开分发的原始资料，可选
  cache/showcases/    # 预先生成的大场景图，可选
  cover.webp          # 官网和模组库显示的封面
  manifest.json      # 工具生成
  README.md          # 工具生成
  browse/            # 工具生成的人物、地点、线索页面
```

目录名必须与 `module.yaml` 的 `id` 一致。每个文件小于 100 MiB；删除无关构建文件、个人配置、密钥和游戏存档。保留作者、来源、许可证与素材署名，只提交你有权公开分发的内容。封面使用无剧透的静态 WebP，建议横幅比例 16:9，文件不得超过 1 MiB。可用原封面或开场画面，署名随素材一同保留。

## 提交

1. Fork 后检出 `mod_library` 分支，添加或修改 `modules/<id>/module/`，并在同级提供 `cover.webp`。
2. 安装 Python 3.12 和 AutoKeeper 0.0.9.7 或更新版本。
3. 运行 `autokeeper check modules/<id>/module`，修复错误；检查警告是否符合设计。
4. 运行 `python tools/catalog.py .`，更新分层目录、预览页面和元数据。
5. 向原仓库的 `mod_library` 分支提交 PR，说明背景、人数、时长、改动和素材来源。PR 检查会验证模组结构。

不要修改根目录的 `index.json` 和 `index.sig`，也不需要签名密钥。模组通过审核并发布目录后，才会出现在游戏的下载列表中。模组插件是可执行代码，会一起审核。

## 维护者发布

先运行目录生成器并提交模组原文件，再对该提交生成下载目录：

```sh
python tools/catalog.py .
git add modules catalog README.md
git commit -m "更新模组内容"
autokeeper library publish-tree . --revision "$(git rev-parse HEAD)"
git add index.json index.sig
git commit -m "发布下载目录"
git push origin mod_library
```

签名私钥只保存在维护者本机配置目录中，不加入仓库或 PR 工作流。下载地址固定到前一个完整 Git 提交，索引和签名在同一次提交中更新。这样在浏览分支继续演进时，客户端下载的仍是一套确定且可校验的文件。
