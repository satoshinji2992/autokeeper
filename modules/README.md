# modules/

- `zhuishuren/`：随仓库附带的示例模组《追书人》，改编自 Chaosium 的 *Paper Chase*，不含原书 PDF。图片来源见 `zhuishuren/art/credits.yaml`。
- `dongxuezhixia/`、`fallout3-youxierenyouyanbujin/`：《冬雪之下》《有些人油盐不进》的同人改编，不含原模组的文字和图片，照片来源见各自的 `art/credits.yaml`。
- `local/<id>/`：导入（游戏里的“导入模组”或 `autokeeper import`）和 `autokeeper generate` 的产物，原文、逐页扫描和导入笔记在它的 `source/` 里。
- `inbox/`：要导入的模组文件可以放这里，游戏里“导入模组”的“模组文件夹”打开的就是它。放在别处也行，在游戏里挑出来或拖进终端就好。

`local/`、`inbox/`、各模组的 `source/`，以及直接丢进这个文件夹的 PDF、Word、zip 等文件，都在 `.gitignore` 里：你自己的模组只留在本地。
一个模组目录里有哪些文件、每个字段的意思，见 [docs/module-format.md](../docs/module-format.md)。
