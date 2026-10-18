# modules/

- `zhuishuren/`：随仓库附带的示例模组《追书人》，改编自 Chaosium 的 *Paper Chase*，不含原书 PDF。图片来源见 `zhuishuren/art/CREDITS.md`。
- `_import/`：把你手上的模组文件（PDF / Word / Markdown）放这里；
- `local/`：`autokeeper import` 与 `autokeeper generate` 的产物。

`_import/` 和 `local/` 都在 `.gitignore` 里：你自己导入的模组只留在本地。
字段说明见 [docs/module-format.md](../docs/module-format.md)。
