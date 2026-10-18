<div align="center">

<img src="docs/img/title.png" width="760" alt="AutoKeeper">

*Ph'nglui mglw'nafh Cthulhu R'lyeh wgah'nagl fhtagn.*

*我以为，世上最仁慈的事，莫过于人类的心智无法把它所知的一切关联起来。*<br>
*我们栖身在一座名为无知的平静小岛上，四周是无垠的黑色海洋，而我们本不该远航。*

<sub><i>H. P. 洛夫克拉夫特，《克苏鲁的呼唤》</i></sub>

<br>

**一位会自己主持《克苏鲁的呼唤》跑团的 AI 守秘人**

[![CI](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Linux%20%C2%B7%20macOS%20%C2%B7%20Windows-555555)
![Models](https://img.shields.io/badge/models-Claude%20%C2%B7%20Codex%20%C2%B7%20DeepSeek%20%C2%B7%20Ollama-D97757)
![License](https://img.shields.io/badge/license-MIT-2EA44F)

[功能](#功能) · [截图](#截图) · [安装](#安装) · [开始游戏](#开始游戏) · [模型与花费](#模型与花费) · [常见问题](#常见问题) · [文档](#文档)

</div>

<p align="center"><img src="docs/img/game.png" width="100%" alt="《追书人》深夜的公墓"></p>

AutoKeeper 是一个在终端里玩的《克苏鲁的呼唤》第 7 版中文跑团。准备一本模组，它就替你当守秘人：描述场景，扮演每一个 NPC，按规则裁定检定，读档时再用评书腔讲一段“上回书说到”。你可以独自调查，可以带上 AI 队友，也可以泡杯茶，看 AI 调查员自己把整个模组跑完。

## 功能

- 导入你手上的模组，PDF、Word、Markdown 都行；没有模组，就给一句灵感，让它查资料、写一个原创的。
- AI 守秘人负责叙事和扮演，骰子交给 BCDice 掷，结果谁也改不了。你说得再动听，检定该掷还得掷。
- 每个 NPC 只知道自己该知道的事，有秘密，会撒谎，也会守口如瓶。
- 彩色 ASCII 画面，照片也能转成字符画。天色随时间变化，墓地入夜起雾，隧道里只有一束手电光，理智下降时画面开始扭曲。
- 兼容常见的骰娘指令，孤注一掷、幸运、理智、疯狂发作、成长一应俱全。
- 线索本、手稿、地图、鼠标手绘，存档随时读取，说书人帮你回顾前情。
- 终端和浏览器都能玩。Claude、Codex、DeepSeek 和本地模型都能当守秘人，断了网也能玩，一局打到一半也能换。
- 还能装进 Claude Code 或 Codex，让它们直接当守秘人。

## 截图

<p align="center"><img src="docs/img/weather.gif" width="100%" alt="场景里的天气：雾、手电、雷雨、哈哈镜"></p>

| 选模组 | 白天的书房 | 手绘场景 | 人物头像 |
|---|---|---|---|
| <img src="docs/img/module.png" alt="选模组"> | <img src="docs/img/game_day.png" alt="白天的书房"> | <img src="docs/img/scenes.png" alt="场景图"> | <img src="docs/img/portraits.png" alt="头像"> |

截图里的《追书人》就附在仓库里，改编自 Chaosium 入门套装里的 *Paper Chase*，装好就能开团。手绘场景和头像来自用 AutoKeeper 生成的原创模组《多一人》。

## 安装

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Node.js 18+。想把照片转成字符画，再装上 [chafa](https://hpjansson.org/chafa/)。

```bash
git clone https://github.com/satoshinji2992/autokeeper && cd autokeeper
uv sync --extra claude --extra graphrag
uv run autokeeper setup
uv run autokeeper doctor        # 检查环境，缺什么会告诉你
```

<details>
<summary>各平台的安装方式</summary>

| | 安装依赖 | 设置 API key |
|---|---|---|
| macOS（zsh / bash） | `brew install uv node chafa` | `export ANTHROPIC_API_KEY=sk-...` |
| Linux（bash / zsh） | `curl -LsSf https://astral.sh/uv/install.sh \| sh`，`apt install nodejs chafa` | `export ANTHROPIC_API_KEY=sk-...` |
| fish | 同上 | `set -gx ANTHROPIC_API_KEY sk-...` |
| Windows（PowerShell） | `winget install astral-sh.uv OpenJS.NodeJS`，`scoop install chafa` | `$env:ANTHROPIC_API_KEY = "sk-..."` |

Windows 上请用 Windows Terminal，旧控制台对真彩色和鼠标的支持不完整。

</details>

## 开始游戏

```bash
uv run autokeeper import 模组.pdf --id mymod          # 导入一本模组
uv run autokeeper generate "1931 年上海，一张没有标签的唱片" --id changpian   # 或者生成一本
uv run autokeeper                                    # 开团
uv run autokeeper serve                              # 在浏览器里玩
uv run autokeeper autoplay modules/local/mymod --party 2   # 看 AI 调查员自己跑团
```

开团后直接用中文说你想做什么就行，比如“我拉开书桌抽屉”“我问守墓人最近见过谁”。

| 操作 | 作用 |
|---|---|
| `.ra 侦查` `.ra 困难聆听` `.rab 侦查` `.rap2 侦查` | 检定、指定难度、奖励骰、惩罚骰 |
| `.sc 1/1d6` `.st 侦查60 hp-2` `.r 1d6+1` `.coc` `.en` `.ti` | 理智、改人物卡、掷骰、掷属性、成长、疯狂发作 |
| F1 · F2 · F3 · F4 · F5 · F6 | 帮助 · 线索本与手稿 · 换模型 · 手绘 · 地图 · 天气动画 |
| Ctrl+R · Ctrl+S · Ctrl+A · Ctrl+T | 上回书说到 · 存档 · 换画法 · 换配色 |

## 模型与花费

默认用你本机 Claude Code 的登录，自己玩不用另外付费。部署给别人用时请设 `ANTHROPIC_API_KEY`，因为 Anthropic 不允许第三方产品提供 claude.ai 登录。

```bash
uv run autokeeper --preset deepseek                   # 用 DeepSeek（需要 DEEPSEEK_API_KEY）
AUTOKEEPER_MODEL=ollama:qwen3:14b uv run autokeeper --search none    # 全部用本地模型，完全离线
uv run autokeeper --model codex:default import 模组.pdf --id mymod    # 用 Codex 订阅导入模组
uv run autokeeper --preset deepseek --fallback ollama:qwen3:14b      # DeepSeek 连不上时换本地模型接着玩
uv run autokeeper --preset deepseek --budget 5                       # 花费超过 5 美元就停
```

用 DeepSeek 跑完一整局入门模组大约一块钱人民币。每个角色用哪个模型，都可以在 `~/.config/autokeeper/config.toml` 里单独设，写法见[配置说明](docs/harness.md#换模型)。

## 在 Claude Code / Codex 里玩

仓库本身就是一个 Claude Code 插件：

```text
/plugin marketplace add satoshinji2992/autokeeper
/plugin install autokeeper@autokeeper
/autokeeper:play mymod
```

Codex 用户在 `~/.codex/config.toml` 里加上：

```toml
[mcp_servers.autokeeper]
command = "uv"
args = ["run", "--directory", "/path/to/autokeeper", "autokeeper", "mcp"]
```

## 常见问题

<details>
<summary>手上没有模组怎么办？</summary>

仓库自带《追书人》和一个只有三个地点的小样例《钟楼》，开团时都能直接选。想玩新的，就用 `autokeeper generate` 给一句灵感，它会查资料、写剧情、画场景，生成一本原创模组。

</details>

<details>
<summary>导入官方模组会被上传吗？</summary>

不会。你自己导入的结果只保存在本地的 `modules/local/`，已被 `.gitignore` 排除。CoC 官方模组受版权保护，请只导入你自己拥有的。

</details>

<details>
<summary>没有网能玩吗？</summary>

能。装好 [Ollama](https://ollama.com/) 和一个本地模型，加上 `--search none`，整局游戏都在本机完成。

</details>

<details>
<summary>画面颜色不对，或者右边一栏不见了？</summary>

不支持真彩的终端（比如 macOS 自带的终端）会自动降到 256 色，换成 iTerm2、WezTerm 或 Windows Terminal 效果最好。终端窄于 96 列时右栏会自动隐藏，按 Ctrl+A 可以在照片和纯 ASCII 两种画法之间切换。

</details>

## 文档

- [Harness 与 Agent 设计](docs/harness.md)
- [评测](docs/eval.md)
- [模组格式](docs/module-format.md)
- [更新日志](CHANGELOG.md)

<details>
<summary>全部命令</summary>

| 命令 | 作用 |
|---|---|
| `autokeeper` / `play` / `serve` | 终端界面 / 浏览器 |
| `import FILE --id ID [--pages 8-19]` | 导入模组 |
| `generate "灵感" --id ID` | 生成原创模组 |
| `research DIR` | 联网核实生成模组里的考据 |
| `check DIR` / `compile DIR` | 校验模组 / 编译成 Skill 包 |
| `index DIR` / `ask DIR "问题"` | 建模组知识库 / 向知识库提问 |
| `art color DIR` / `art show DIR NAME` | 给 ASCII 图上色 / 预览 |
| `autoplay DIR --party N` | AI 调查员自动跑团 |
| `setup` / `doctor [--ping]` | 安装 / 检查环境 |
| `trace` | 查看每个 Agent 的用量和花费 |
| `eval context` / `eval rhetoric` / `eval lore` | 跑评测 |
| `mcp` | 以 MCP 服务运行 |

全局选项有 `--preset`、`--model`、`--fallback`、`--search`、`--budget`。

</details>

## 路线图

- [x] 模组导入与原创生成
- [x] 终端、浏览器、Claude Code、Codex 四种玩法
- [x] Claude、Codex、DeepSeek、本地模型都能当守秘人
- [x] 彩色 ASCII、照片字符画、昼夜与天气
- [ ] 完整的战斗与追逐规则
- [ ] QQ / Discord 机器人，多人联机
- [ ] 语音叙事

## 参与贡献

欢迎提 issue 和 PR，不管是 bug、新模组格式的支持，还是更好看的 ASCII 画。提交前请跑一遍测试：

```bash
uv run ruff check src tests && uv run pytest
```

## 致谢

骰子与规则来自 [BCDice](https://github.com/bcdice/BCDice)；Agent 编排基于 [LangGraph](https://github.com/langchain-ai/langgraph) 与 [LangChain](https://github.com/langchain-ai/langchain)；模组知识库用的是 [LightRAG](https://github.com/HKUDS/LightRAG)；界面基于 [Textual](https://github.com/Textualize/textual)；照片转字符画靠 [chafa](https://hpjansson.org/chafa/)。

## 许可

代码以 [MIT](LICENSE) 协议发布，`src/autokeeper/module/example/` 是 CC0 的格式样例。《追书人》是 *Paper Chase*（Bret Kramer 著，Chaosium 出版）的同人改编，原作版权归 Chaosium，仓库不含原书的 PDF；它的场景照片来自 Wikimedia Commons，作者和授权见 [CREDITS](modules/zhuishuren/art/CREDITS.md)。Call of Cthulhu 是 Chaosium Inc. 的商标，本项目与 Chaosium 无关。
