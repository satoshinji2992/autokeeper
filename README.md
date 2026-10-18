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

[功能](#功能) · [截图](#截图) · [安装](#安装) · [开始游戏](#开始游戏) · [和朋友一起玩](#和朋友一起玩) · [模型与花费](#模型与花费) · [常见问题](#常见问题) · [文档](#文档)

</div>

<p align="center"><img src="docs/img/game.png" width="100%" alt="《追书人》深夜的公墓"></p>

AutoKeeper 是一个在终端里玩的《克苏鲁的呼唤》第 7 版中文跑团。准备一本模组，它就替你当守秘人：描述场景，扮演每一个 NPC，按规则裁定检定，读档时再帮你回顾一遍前情。你可以独自调查，可以带上 AI 队友，可以叫上朋友在局域网里同桌，也可以泡杯茶，看 AI 调查员自己把整个模组跑完。

## 功能

- 导入你手上的模组：在标题画面选“导入模组”，挑一个文件或者直接拖进终端，PDF、Word、Markdown、打包的 zip 都行，导完就能开团。没有模组，就给一句灵感，让它查资料、写一个原创的。
- AI 守秘人负责叙事和扮演，骰子交给 BCDice 掷，结果谁也改不了。你说得再动听，检定该掷还得掷。
- 守秘人会即兴。你想做模组没写到的事，它不会说“不行”，而是顺势加人物、加线索、开支线，必要时多出一个结局；要是乱来，它会先演一段，再让你尝到后果。新剧情在后台构思，写好了直接插进来，不耽误守秘人回应你。
- 开局时它会读你的调查员背景，把你牵挂的人和事织进故事，模组里体验不好的地方也会顺手补上。剧情里有大大小小的冲突，NPC 的态度会随着故事改变。
- 线索不憋也不塞。查对了具体的东西就能拿到，笼统地看一圈只会注意到最显眼的；关键线索失败了还能再找，再失败也能到手，只是要付出代价。
- 车卡不用从零填表：说一位公众人物的名字，比如鲁迅、居里夫人、立石凛，它会上网查这个人、找几张照片让你挑，问你三个问题，再按模组的年代把他车成调查员；也可以直接导入你已有的卡，骰娘的 `.st` 指令、车卡器导出的表格、PDF 或 Word 人物卡、纸质卡的照片，甚至酒馆的角色卡都行。
- 每个 NPC 只知道自己该知道的事，有秘密，会撒谎，也会守口如瓶。
- 彩色 ASCII 画面：照片转成字符画铺满画面，再描一层 ASCII 轮廓线，深夜也认得出房子和墓碑，终端越大画面越大。天色随时间变化，墓地入夜起雾，隧道里只有一束手电光，理智下降时画面开始扭曲。
- 兼容常见的骰娘指令，孤注一掷、幸运、理智、疯狂发作、成长一应俱全。
- 线索本、手稿、只画出你知道的地方的地图、完整的人物卡、鼠标手绘，存档随时读取，读档时先回顾一遍前情。
- 和朋友在局域网里开一桌：朋友用浏览器打开一个地址就能入座，每位调查员由人或 AI 扮演，有人掉线，AI 先替他走。
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
uv run autokeeper import 模组.pdf                     # 导入一本模组（游戏里的“导入模组”也行）
uv run autokeeper generate "1931 年上海，一张没有标签的唱片" --id changpian   # 或者生成一本
uv run autokeeper                                    # 开团
uv run autokeeper serve                              # 在浏览器里玩
uv run autokeeper autoplay modules/local/mymod --party 2   # 看 AI 调查员自己跑团
```

开团后直接用中文说你想做什么就行，比如“我拉开书桌抽屉”“我问守墓人最近见过谁”。要不要掷骰由守秘人决定，需要时会先告诉你掷什么、要掷到多少、为什么，你按回车或空格亲手掷。

| 操作 | 作用 |
|---|---|
| `.ra 侦查` `.rab 恐吓` | 想在这里用某个技能：守秘人按情境决定掷不掷、多难；同样的尝试失败了不能重掷 |
| `.r 1d6+1` `.coc` `.st` | 掷骰、掷一组属性、看人物卡（不能改） |
| `.en` | 结局后做成长检定，每局一次 |
| F1 · F2 · F3 · F4 · F5 · F6 · F7 | 帮助 · 线索本与手稿 · 换模型 · 手绘 · 地图 · 天气动画 · 人物卡 |
| Ctrl+R · Ctrl+S · Ctrl+A · Ctrl+T | 前情回顾 · 存档 · 换画法 · 换配色 |

## 和朋友一起玩

同一个网络里（比如同一个 Wi-Fi），一个人开房间，其他人用浏览器加入，什么都不用装。

```bash
uv run autokeeper host                                         # 默认《追书人》：第一位调查员留给玩家，其余交给 AI
uv run autokeeper host modules/local/mymod --seats 玩家,玩家,AI   # 按预设调查员的顺序安排座位
uv run autokeeper join 192.168.1.5                              # 装了 AutoKeeper 的朋友也可以在终端里加入
```

开好房间后，终端会打印一个地址，比如 `http://192.168.1.5:8000`。把它发给朋友，打开后填个名字，挑一个空座。人齐了自动开局，也可以选“现在开始”，空着的座位交给 AI 调查员。

大家按座位轮流行动，轮到你时输入框才算数。全角括号开头的话是场外聊天，比如“（我去倒杯水）”，所有人都看得到，不算行动。有人掉线，AI 先替他走这一回合，他重新连上、坐回原来的座位就能接着玩。

游戏跑在房主的电脑上，用房主的模型和额度，每回合自动存档，下次用 `--load` 接着玩。模组的真相、NPC 的秘密和还没找到的线索只留在房主那边，朋友的屏幕上只有调查员该看到的东西。

## 模型与花费

默认用你本机 Claude Code 的登录，自己玩不用另外付费。部署给别人用时请设 `ANTHROPIC_API_KEY`，因为 Anthropic 不允许第三方产品提供 claude.ai 登录。

```bash
uv run autokeeper --preset deepseek                   # 用 DeepSeek（需要 DEEPSEEK_API_KEY）
AUTOKEEPER_MODEL=ollama:qwen3:14b uv run autokeeper --search none    # 全部用本地模型，完全离线
uv run autokeeper --model codex:default import 模组.pdf --id mymod    # 用 Codex 订阅导入模组
uv run autokeeper --preset deepseek --fallback ollama:qwen3:14b      # DeepSeek 连不上时换本地模型接着玩
uv run autokeeper --preset deepseek --budget 5                       # 花费超过 5 美元就停
```

想要剧情更细，就加 `--mode 高级剧情`，守秘人、导演、NPC 和 AI 队友都会多想一层；想要快一点，就加 `--mode 经济`。游戏里按 F3 也能换。用 DeepSeek 实测 12 回合，高级剧情多花七成的钱、六成的时间；经济模式每一步快一些，但省不了多少钱，因为钱主要花在读上下文上。

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
<summary>想严格按模组玩，不要守秘人即兴？</summary>

开团时加上 `--improv 严格`，或者在 `~/.config/autokeeper/config.toml` 里写 `improv = "严格"`。守秘人就只用模组里写好的人物、线索和结局。

</details>

<details>
<summary>导入官方模组会被上传吗？</summary>

不会。导入的成品只保存在本地的 `modules/local/`，随手丢进 `modules/` 的 PDF、zip 这些原文件也都在 `.gitignore` 里，不会进仓库。CoC 官方模组受版权保护，请只导入你自己拥有的。

</details>

<details>
<summary>没有网能玩吗？</summary>

能。装好 [Ollama](https://ollama.com/) 和一个本地模型，加上 `--search none`，整局游戏都在本机完成。

</details>

<details>
<summary>朋友连不上房间？</summary>

先确认大家在同一个网络里，再看房主电脑的防火墙有没有放行 8000（网页）和 8765（终端加入）这两个端口。在 WSL2 里开房间，需要在 Windows 的 `.wslconfig` 里打开镜像网络（`[wsl2]` 下写 `networkingMode=mirrored`），否则别的电脑连不进 WSL。

</details>

<details>
<summary>画面颜色不对，或者右边一栏不见了？</summary>

不支持真彩的终端（比如 macOS 自带的终端）会自动降到 256 色，换成 iTerm2、WezTerm 或 Windows Terminal 效果最好。终端窄于 96 列时右栏会自动隐藏。Ctrl+A 在四种画法间切换：块字符加轮廓线（默认）、更细的六分块（终端字体不支持时会显示成方框，macOS 自带终端就是这样）、不描轮廓的照片、纯 ASCII；也可以在配置里写 `art_style = "fine"`。

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
| `host [DIR] [--seats 玩家,AI]` / `join 地址` | 开局域网房间 / 加入房间 |
| `setup` / `doctor [--ping]` | 安装 / 检查环境 |
| `trace` | 查看每个 Agent 的用量和花费 |
| `eval context` / `eval rhetoric` / `eval lore` | 跑评测 |
| `mcp` | 以 MCP 服务运行 |

全局选项有 `--preset`、`--model`、`--fallback`、`--search`、`--budget`、`--improv`。

</details>

## 路线图

- [x] 模组导入与原创生成
- [x] 终端、浏览器、Claude Code、Codex 四种玩法
- [x] Claude、Codex、DeepSeek、本地模型都能当守秘人
- [x] 彩色 ASCII、照片字符画、昼夜与天气
- [ ] 完整的战斗与追逐规则
- [x] 局域网联机，人和 AI 调查员同桌
- [ ] QQ / Discord 机器人
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
