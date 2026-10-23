# Windows 应用打包

安装版和免安装版共用一套文件：`AutoKeeper.exe` 启动内置 Python，保留原有 Textual 界面；Node.js、锁定的 BCDice、图片渲染库、模组和共享技能随包提供。运行时不调用 uv、pip、npm 或安装脚本。模型连接仍需用户自行配置。

保留完整解释器而非冻结 Python：网页联机、插件沙箱和骰子窗口都会启动 Python 子进程。数据仍使用 `paths.py` 的用户目录，不写入应用目录。第一次运行只将掷骰组件复制到用户目录，按包锁文件更新，并使用文件锁避免两个窗口同时复制。

在 Windows x64 上准备 Python 3.12 和 uv，再运行（构建工具不会进入发行包）：

```powershell
python desktop/build_windows.py --wheel dist/autokeeper-0.0.5.6-py3-none-any.whl --python-root "C:\path\to\standalone-python" --msvc-runtime "C:\path\to\msvc-x64"
```

`--python-root` 必须是可复制的独立解释器，不是 venv。`--msvc-runtime` 是从 [Microsoft 官方签名的 VC Redist](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) 提取的 x64 最小运行库目录，包含 `msvcp140*.dll`、`vcruntime140*.dll` 和原始许可、来源记录；不从开发机的 System32 复制。它们放在解释器旁边，不安装到玩家系统。默认带 Claude SDK；`--without-claude` 可省略。

构建会校验 Node 官方归档的 SHA-256，使用 npm 锁文件安装骰子组件，并执行真实 Windows 离线验收：版本、三个模组、图片转字符、掷骰、插件及浏览器子进程、标题与设置页、ONNX 文档识别及运行库加载位置。它使用临时用户目录，不读取开发者的存档或密钥。ZIP 内保留第三方许可证，并列出 `components.json`；可运行 `AutoKeeper.exe --self-test` 重检。

构建安装版需另装 [Inno Setup](https://jrsoftware.org/isdl.php)。使用相同的已验收目录：

```powershell
ISCC /DAppVersion=0.0.5.6 /DAppSource="C:\path\to\AutoKeeper-0.0.5.6-windows-x64" /DOutputFolder="C:\path\to\dist" desktop/installer.iss
```

安装无需管理员权限，创建桌面和开始菜单快捷方式。卸载不删除 `%LOCALAPPDATA%\autokeeper` 和 `%APPDATA%\autokeeper` 中的存档、配置。不要将个人配置、账号、缓存或 `modules/*/source/` 加入发行包。发布 ZIP、安装 EXE 及各自的 SHA-256 校验文件。

`ChineseSimplified.isl` 来自 Inno Setup 的官方翻译，原作者与来源保留在文件头；上游地址为 `https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseSimplified.isl`。
