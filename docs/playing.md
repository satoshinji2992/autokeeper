# 游玩指南

[回到 README](../README.md) · [安装依赖](#安装依赖) · [模型与花费](#模型与花费) · [操作](#操作) · [联机](#联机) · [模组](#导入生成与观战) · [常见问题](#常见问题)

## 安装依赖

需要 Python 3.12+、uv 和 Node.js 18+。chafa 用来显示照片字符画，没装时使用手绘图。

| 系统 | 安装方式 |
|---|---|
| macOS | `brew install uv node chafa` |
| Linux | 按 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装 uv；Node.js、chafa 可用发行版包管理器安装，例如 `sudo apt install nodejs npm chafa`。检查 `node --version` 不低于 18。 |
| Windows | 用 `winget install astral-sh.uv`、`winget install OpenJS.NodeJS` 安装 uv 和 Node.js；使用 Scoop 的话，可用 `scoop install chafa` 安装 chafa。建议在 Windows Terminal 中运行。 |

在仓库目录执行：

```bash
uv sync --extra claude --extra graphrag
uv run autokeeper setup
uv run autokeeper doctor
```

`setup` 会安装 BCDice 的 Node 桥接、编译模组，并为 Claude Code / Codex 链接仓库里的 Skills。`graphrag` 安装模组原文检索需要的 LightRAG 和本地嵌入依赖；首次使用可能需要下载嵌入模型。

更新代码后运行 `uv sync --extra claude --extra graphrag`，再运行 `setup` 和 `doctor`。从 0.0.1 升级前请备份 `saves/`：0.0.2 不再读取旧存档，自己的角色卡、本地模型和知识库会自动迁移到 `data/`。

## 模型与花费

默认使用本机的 Claude Code 登录，也可以设置 `ANTHROPIC_API_KEY`。DeepSeek 需要 `DEEPSEEK_API_KEY`。不同终端设置环境变量的写法如下，把示例内容换成自己的密钥：

```bash
# bash / zsh
export DEEPSEEK_API_KEY="你的密钥"
```

```fish
# fish
set -gx DEEPSEEK_API_KEY "你的密钥"
```

```powershell
# PowerShell
$env:DEEPSEEK_API_KEY = "你的密钥"
```

使用 Anthropic API 时，变量名改成 `ANTHROPIC_API_KEY`。密钥也可以放进仓库中已忽略的 `.env`，不要提交到 Git。

```bash
uv run autokeeper --preset deepseek
uv run autokeeper --model codex:default
uv run autokeeper --model ollama:qwen3:14b --search none
```

Codex 使用本机已登录的 CLI；Ollama 需要提前下载指定模型。标题页的「设置」和游戏中的 F9 可以换模型、备用模型、思考模式；导入界面还能为本次导入单独指定模型与读图模型。设置会保存。更细的分工写在 `~/.config/autokeeper/config.toml`，见[模型配置](harness.md#换模型)。

费用和订阅额度由所选服务计算。Ctrl+B 查看本局用量和估算花费；未标价模型可能没有金额估算，这里的数字也不是服务商账单。上下文、回合数、模型和配图都会影响消耗。

```bash
# 主模型不可用时，尝试本地备用模型
uv run autokeeper --preset deepseek --fallback ollama:qwen3:14b

# 给模型调用设置 5 美元的估算预算
uv run autokeeper --preset deepseek --budget 5
```

`--mode 高级剧情` 提高叙事相关角色的思考强度，`--mode 经济` 降低思考强度；效果和速度取决于模型。叙事已经开始输出时不会中途换到备用模型，避免重讲一遍；出错后用 `/retry` 继续。[重试与预算的实现](harness.md)里有详细说明。

### 离线游玩

提前安装依赖、下载 Ollama 模型；把所有使用的模型角色都设为本地模型，关闭搜索（`--search none`）和设置中的「AI 画图」。已有图片与字符画仍可显示，联网找照片和云端模型不可用。

## 操作

启动终端界面用 `uv run autokeeper`；浏览器界面用 `uv run autokeeper serve`。在标题页选「新的调查」，选模组、调查员和同伴即可开局。用自然语言描述行动或直接说话，不必先记指令。需要检定时，界面会请你掷骰；守秘人讲的时候也能先输入行动，讲完自动交上。

| 按键 | 作用 |
|---|---|
| F1 | 玩法说明 |
| F2 / F3 / F4 | 线索本与手稿 / 地图 / 人物卡 |
| F5 | 提供三个可修改的行动思路 |
| F6 | 重讲最新一段，保留已经结算的骰子和线索 |
| F7 或 Ctrl+R | 前情回顾 |
| F8 | 手绘 |
| F9 | 模型设置（标题页也是 F9） |
| F10 | 结局后复盘 |
| Ctrl+S | 存档 |
| Ctrl+A / Ctrl+T | 换画法 / 换配色 |
| Ctrl+O | 叙事偏好：篇幅、节奏、恐怖程度与略去的细节 |
| Ctrl+B | 用量与估算花费 |
| Ctrl+K | 查看规则插件或添加家规 |
| Ctrl+G / Ctrl+Y | 切换右栏「经过 / 进展 / 背景」 / 现在整理进展 |
| Esc | 菜单，包括存读档、设置、返回标题和退出 |

| 输入 | 作用 |
|---|---|
| `.ra 侦查`、`.rab 恐吓` | 提议使用技能，守秘人仍会按情境决定是否检定与难度；失败的同一次尝试不能随意重掷 |
| `.r 1d6+1`、`.coc`、`.st` | 掷骰 / 掷一组属性 / 查看人物卡 |
| `.en` | 结局后做成长检定，每局一次 |
| `/retry` | 从中断的阶段继续，保留已结算的结果和 NPC 回答 |

浏览器会占用部分功能键，例如 F5 刷新。可点击底栏、Esc 菜单，或输入 `/suggest`、`/sheet`、`/notes`、`/map`、`/help`。刷新网页后，联机玩家需要重新入座。

### 人物、记录和画面

标题页「角色卡」可以查看自己的调查员、存档里的调查员和模组预设。支持 AI 协助车卡、导入、复制、导出和删除；已有卡片可编辑名字与背景等文字，不能直接修改数值。游戏里的人物卡还记录本局经历。

「继续调查」列出模组、调查员、时间、地点与回合数，可以预览、改名、复制或删除。删除的存档和角色卡放进 `data/games/trash/`。

右栏的「经过」按行动列出记录，点一下可以跳到对应叙述；「进展」在有新发现时后台整理，两次至少隔六轮；「背景」保留开场时知道的事。地图只画已知地点和道路，可展开内部地点；选中地点按 G，会在输入框里写好前往那里的行动。

默认用块字符画显示照片，Ctrl+A 可切换轮廓、六分块和纯 ASCII 等画法。六分块需要字体支持。终端窄于 96 列时，右栏自动隐藏；不支持真彩的终端会降为 256 色。使用支持真彩和中文等宽显示的终端，画面会更清楚。

即兴配图在后台准备，先显示简图或已有画面，完成后更新。开场、危机、关键地点等时刻可显示整屏「大场景」，按键、点击或八秒后收起，掷骰时让路；图片没准备好就跳过。两种配图都可以在设置中关闭。

「AI 画图」需要本机安装并登录 Codex，按参考图或描述生成，并随剧情重画明显变化的地点和人物。上限是每个模组 100 张、每局 12 张；生成也会消耗相应服务的额度。`doctor` 会显示是否可用，故障处理见游戏内玩法说明的「遇到问题」。

## 联机

同一局域网里，房主从标题页「开房间」选模组，安排玩家、AI 或不上场的座位；不够就「加一个空座位」。也可以选择联机存档继续。准备房间里有要发给朋友的网址，朋友在浏览器里打开、填名字、挑座位。房主自己也在准备房间入座。

坐下后按 R「选角色」，可用自己的卡、模组预设，或新建、导入。车卡调用房主的模型，朋友不用配置密钥；用终端加入时，车好的卡也会存进自己电脑的「我的角色」。按 Y「准备好了」，全员准备后开局；「现在开始」会把空座交给 AI。开局后只能回到现有座位，不能更换角色；续档沿用原来的调查员。

### 一轮怎么进行

每位玩家各提交一件事，先后不限；此外还有一个共用的额外行动位，谁先提交谁占用。输入 `/undo` 撤回，空着输入框按回车表示说完了。大家说完，守秘人按提交顺序结算和叙述，AI 同伴也会行动。

守秘人讲的时候提交的行动排到下一轮最前面。全角括号开头是场外聊天，例如 `（我去倒杯水）`，不算行动。掉线后由 AI 暂时接手，重新连上并回到原座位即可继续。

房主的名字后有「房主」标记，可以 F9 换模型，当前一段讲完后生效。浏览器加入的人是普通玩家。每回合自动存档，游戏和模型调用都在房主电脑上；未发现的线索、NPC 秘密和模组真相不会发给玩家。

离开准备房间不会关房；标题页「回到房间」能返回。要关闭，请在准备房间或游戏的 Esc 菜单里选「关闭房间」，或退出程序。

### 命令行开房间

```bash
uv run autokeeper host
uv run autokeeper host modules/local/mymod --seats 玩家,玩家,AI
uv run autokeeper join 192.168.1.5
```

默认模组是《追书人》，第一位调查员留给玩家，其余交给 AI。开房间的终端不占座，房主自己另开终端 `uv run autokeeper join 127.0.0.1`。如果程序显示了不同端口，请连显示的完整地址。续档用 `host --load 存档名`。

浏览器加入无需安装。终端加入要安装 AutoKeeper；图片由各自电脑上的 chafa 转成字符画，没装则只显示手绘图。

### 连接排查

先打开准备房间「朋友进不来？」或玩法说明里的联机排查页，它会列出本机各网络地址和处理步骤。

- 确认双方在同一个局域网。异地可通过 Radmin VPN 或 ZeroTier 组成虚拟局域网，使用那个网络分配的地址。
- 防火墙允许 Python 联网。默认网页端口 8000、终端联机端口 8765；端口被占会换号，以房间里显示的为准。
- WSL 开房间时，在 Windows 的 `.wslconfig` 的 `[wsl2]` 下配置 `networkingMode=mirrored`，重启 WSL 后再试。
- 双方版本需要一致。更新后重新同步依赖。
- 开不了新房间时，看标题页是否已有「回到房间」。命令行开的房间在原窗口按 Ctrl+C 关闭。

## 导入、生成与观战

标题页可以导入 PDF、Word、Markdown、文本或 ZIP，也可以使用命令行：

```bash
uv run autokeeper import 模组.pdf --id mymod
uv run autokeeper import 模组.pdf --model codex:default --vision-model codex:default
uv run autokeeper generate "1931 年上海，一张没有标签的唱片" --id changpian
uv run autokeeper autoplay modules/local/mymod --party 2
```

`--model` 和 `--vision-model` 分别选择导入与读图模型。导入结束会查找 Wikimedia Commons 上有授权的照片，转成字符画；加 `--no-photos` 跳过，之后可用 `autokeeper art fetch 模组目录` 补图。模组原图和手稿尽量保留；`generate` 从设定生成原创模组，`autoplay` 让 AI 调查员自动游玩，标题页「观战」也能看。

**导入成品在 `modules/local/`，原文在对应模组的 `source/`。** 使用云端模型时，导入和游玩所需的正文、图片会发送给所选服务。Git 忽略规则只防止提交文件，不代表内容不会发送给模型。请只导入自己有权使用的材料，不要公开分享受版权保护的原文和图片。

## 在 Claude Code / Codex 中主持

仓库也是一个 Claude Code 插件：

```text
/plugin marketplace add satoshinji2992/autokeeper
/plugin install autokeeper@autokeeper
/autokeeper:play mymod
```

Codex 可在 `~/.codex/config.toml` 配置 MCP 服务，把路径换成实际仓库位置：

```toml
[mcp_servers.autokeeper]
command = "uv"
args = ["run", "--directory", "/path/to/autokeeper", "autokeeper", "mcp"]
```

## 常见问题

**想严格按模组玩。** 标题页「设置」把自由度改成「严格按模组已有内容」，或启动时加 `--improv 严格`；配置文件也支持 `improv = "严格"`。

**动画结束后没动静，或回复中断。** 先看输入框和状态提示，确认是否在等掷骰或选择。模型请求失败时用 `/retry`，或菜单里的「接着讲」。如果仍然卡住，保留报错和 `data/logs/` 中相关记录，提 issue 前删掉密钥与私人内容。

**原图怎么看？** 手稿发现后可在窗口中读完整文字、看字符画预览，点击「查看原图」；F2 可以重看。尚未发现的内容不会提前解锁。

**数据在哪里？** 仓库的 `data/games/` 是存档，`data/cards/` 是自己的调查员，`data/logs/` 是运行记录；配置在 `~/.config/autokeeper/`。备份时同时保存自己的导入模组 `modules/local/`。`data/cache/` 包括嵌入模型、知识库、预处理图片和大场景图，可以重建，但重新下载、建库或生成图片可能需要联网和额外额度；有想保留的生成图，清理前先备份。详见[文件位置](harness.md#文件放在哪里)。

## 命令索引

以下命令均接在 `uv run autokeeper` 后；参数细节可用 `--help` 查看。

| 命令 | 用途 |
|---|---|
| `play` / `serve` | 终端 / 浏览器游玩 |
| `host [DIR] [--seats 玩家,AI]` / `join 地址` | 开房间 / 加入房间 |
| `import FILE --id ID [--pages 8-19]` | 导入模组，可指定页码 |
| `generate "灵感" --id ID` / `research DIR` | 生成模组 / 联网核实考据 |
| `check DIR` / `compile DIR` | 校验模组 / 编译 Skill 包 |
| `index DIR` / `ask DIR "问题"` | 建原文知识库 / 提问 |
| `art fetch DIR` / `art color DIR` / `art show DIR NAME` | 补照片 / 字符画上色 / 预览 |
| `showcase 模组` | 提前准备大场景图 |
| `autoplay DIR --party N` | AI 调查员自动跑团 |
| `plugin list DIR` / `plugin test DIR` | 查看规则插件 / 运行插件测试 |
| `setup` / `doctor [--ping]` | 准备环境 / 检查环境（可实测连接） |
| `trace` | 查看模型用量与花费 |
| `eval context` / `eval rhetoric` / `eval lore` / `eval style` | 运行评测 |
| `mcp` | 运行 MCP 服务 |

`--preset`、`--model`、`--fallback`、`--search`、`--budget`、`--improv` 等全局选项放在子命令前。例如 `uv run autokeeper --preset deepseek import 模组.pdf`。导入子命令还可以用自己的 `--model` 覆盖本次导入的选择。
