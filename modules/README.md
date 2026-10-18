# modules/

- `zhuishuren/`：随仓库附带的示例模组《追书人》，改编自 Chaosium 的 *Paper Chase*，不含原书 PDF。图片来源见 `zhuishuren/art/CREDITS.md`。
- `dongxuezhixia/`、`fallout3-youxierenyouyanbujin/`：《冬雪之下》《有些人油盐不进》的同人改编，不含原模组的文字和图片，照片来源见各自的 `art/CREDITS.md`。
- `local/`：导入（游戏里的“导入模组”或 `autokeeper import`）和 `autokeeper generate` 的产物。

要导入的模组文件放哪都行，在游戏里挑出来或拖进终端就好。直接丢进这个文件夹的 PDF、Word、zip 等文件，以及 `local/`、`_import/`，都在 `.gitignore` 里：你自己的模组只留在本地。
字段说明见 [docs/module-format.md](../docs/module-format.md)。
