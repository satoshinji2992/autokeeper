# 游玩指南

[回到 README](../README.md) · [安装](#安装) · [模型与花费](#模型与花费) · [操作](#操作) · [联机](#联机) · [模组](#导入生成与观战) · [常见问题](#常见问题)

## 安装

装好 [uv](https://docs.astral.sh/uv/getting-started/installation/) 以后一条命令：

```bash
uv tool install autokeeper
```

用 Claude 的话装成 `uv tool install "autokeeper[claude]"`（DeepSeek、OpenAI 兼容接口和本地模型不需要）。用 pipx 也行：`pipx install autokeeper`。PyPI 上还没有新版本时，可以直接从 GitHub 装：`uv tool install git+https://github.com/satoshinji2992/autokeeper`。

装好以后在任意目录输入 `autokeeper` 开始。第一次运行会把掷骰组件装进你的用户目录，再问你用哪家模型、填 API Key。想提前做完这些，运行 `autokeeper setup`。

还没装 uv，可以用一键脚本，它先装 uv，再装 autokeeper、运行 `setup`：

```bash
# Linux、macOS、WSL
curl -fsSL https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.sh | bash
```

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
```

再运行一次脚本，或者 `uv tool upgrade autokeeper`（pipx 是 `pipx upgrade autokeeper`），就是更新。脚本可以用环境变量调整：`AUTOKEEPER_VERSION` 装指定版本（如 `0.0.3`），`AUTOKEEPER_EXTRAS=graphrag` 加上模组原文检索（约 1 GB，首次使用还会下载嵌入模型），`AUTOKEEPER_FROM=git` 和 `AUTOKEEPER_REF` 从 GitHub 的某个分支或标签装。不用脚本的话，原文检索这样加：`uv tool install "autokeeper[graphrag]"`。

另外要装的是 Node.js 18+ 和 chafa。一键脚本会顺便用系统的包管理器装 chafa（macOS 用 brew，Linux 用 apt、dnf 或 pacman，Windows 用 Scoop）；用 `uv tool install` 装的话，第一次运行或 `autokeeper setup` 时，Windows 和 Linux 会自动下载 chafa 的官方版本，macOS 请自己 `brew install chafa`。没有 Node.js，伤害和理智的骰子掷不了；没有 chafa，照片显示成手绘字符画。

| 系统 | 安装方式 |
|---|---|
| macOS | `brew install node chafa` |
| Linux | `sudo apt install nodejs npm chafa`（其他发行版用自己的包管理器）。检查 `node --version` 不低于 18。 |
| Windows | `winget install OpenJS.NodeJS.LTS`；用 Scoop 的话 `scoop install chafa`。建议在 Windows Terminal 中运行。 |

换模型、补 Key：`autokeeper login`。检查环境：`autokeeper doctor`，加 `--ping` 给每个模型发一句话试试通不通。

### 东西放在哪

| | Linux、macOS | Windows |
|---|---|---|
| 设置和 API Key（`config.toml`、`.env`，只有你能读） | `~/.config/autokeeper/` | `%APPDATA%\autokeeper\` |
| 存档、角色卡、导入的模组 | `~/.local/share/autokeeper/` | `%LOCALAPPDATA%\autokeeper\` |
| 缓存（本地模型、知识库、图片，可以删） | `~/.cache/autokeeper/` | `%LOCALAPPDATA%\autokeeper\Cache\` |
| 导出的角色卡 | `~/Documents/AutoKeeper/角色卡导出/` | 「文档」里的 `AutoKeeper\角色卡导出\` |

Linux 和 macOS 上设置了 `XDG_CONFIG_HOME`、`XDG_DATA_HOME`、`XDG_CACHE_HOME` 的话，按它们来。`autokeeper doctor` 会列出存档和设置的位置。想把所有东西放在一个文件夹里，设置 `AUTOKEEPER_HOME`，里面的布局和源码目录一样。

### 以前从源码目录玩的存档

从源码目录（`uv run autokeeper`）玩的存档在那个目录的 `data/` 里。装好的 `autokeeper` 第一次运行时，会把记得的源码目录和当前目录里的存档、角色卡、导入的模组复制过来；原处不动，已有的不覆盖。之后想再带一次，或者源码目录没被找到：

```bash
autokeeper migrate ~/autokeeper    # 换成你的源码目录
```

用过 0.0.3 测试版一键脚本的，旧的安装目录会被移到 `~/.local/share/autokeeper-old-install`（Windows 在 `%LOCALAPPDATA%` 里），里面的存档同样会复制过来，确认没问题后可以删掉它。

### 从源码开发

```bash
git clone https://github.com/satoshinji2992/autokeeper
cd autokeeper
uv sync --extra graphrag
uv run autokeeper setup
uv run autokeeper doctor
```

从源码目录运行时，模组、存档和掷骰组件都在仓库里（`modules/`、`data/`、`bridge/`），和以前一样。`setup` 会安装 BCDice 的 Node 桥接、编译模组、为 Claude Code / Codex 链接仓库里的 Skills，最后问用哪家模型。更新代码后运行 `uv sync`，再运行 `setup` 和 `doctor`。从 0.0.1 升级前请备份 `saves/`：0.0.2 不再读取旧存档，自己的角色卡、本地模型和知识库会自动迁移到 `data/`。

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

使用 Anthropic API 时，变量名改成 `ANTHROPIC_API_KEY`。`autokeeper login` 会把密钥存进设置文件夹里的 `.env`；从源码目录运行时，也可以放进仓库中已忽略的 `.env`，不要提交到 Git。

```bash
autokeeper --preset deepseek
autokeeper --model codex:default
autokeeper --model ollama:qwen3:14b --search none
```

Codex 使用本机已登录的 CLI；Ollama 需要提前下载指定模型。标题页的「设置」和游戏中的 F9 可以换模型、备用模型、思考模式；导入界面还能为本次导入单独指定模型与读图模型。设置会保存。更细的分工写在 `~/.config/autokeeper/config.toml`，见[模型配置](harness.md#换模型)。

费用和订阅额度由所选服务计算。Ctrl+B 查看本局用量和估算花费；未标价模型可能没有金额估算，这里的数字也不是服务商账单。上下文、回合数、模型和配图都会影响消耗。

```bash
# 主模型不可用时，尝试本地备用模型
autokeeper --preset deepseek --fallback ollama:qwen3:14b

# 给模型调用设置 5 美元的估算预算
autokeeper --preset deepseek --budget 5
```

`--mode 高级剧情` 提高叙事相关角色的思考强度，`--mode 经济` 降低思考强度；效果和速度取决于模型。叙事已经开始输出时不会中途换到备用模型，避免重讲一遍；出错后用 `/retry` 继续。[重试与预算的实现](harness.md)里有详细说明。

### 离线游玩

提前安装依赖、下载 Ollama 模型；把所有使用的模型角色都设为本地模型，关闭搜索（`--search none`）和设置中的「AI 画图」。已有图片与字符画仍可显示，联网找照片和云端模型不可用。

## 操作

启动终端界面用 `autokeeper`；浏览器界面用 `autokeeper serve`（从源码目录运行时前面加 `uv run`）。在标题页选「新的调查」，选模组、调查员和同伴即可开局。开局先播放一小段开局演出，介绍故事背景，按任意键看下一幕、Esc 跳过；演出结束后守秘人开始讲开场。用自然语言描述行动或直接说话，不必先记指令。需要检定时，界面会请你掷骰；守秘人讲的时候也能先输入行动，讲完自动交上。

| 按键 | 作用 |
|---|---|
| F1 | 玩法说明 |
| F2 / F3 / F4 | 线索本与手稿 / 地图 / 人物卡 |
| F5 | 提供三个可修改的行动思路 |
| F6 | 重讲最新一段，保留已经结算的骰子和线索 |
| F7 或 Ctrl+R | 前情回顾 |
| F8 | 手绘 |
| F9 | 模型设置：「模型方案」页选用哪家、填 API Key、思考强度；「各分工模型」页给守秘人、裁定、导演等单独指定模型（标题页也是 F9） |
| F10 | 结局后复盘 |
| F12 | 开发者模式：顶栏变色，叙述下方显示本轮调用、检定、人物实际位置和接下来的日程；再按一次退出 |
| Ctrl+S | 存档 |
| Ctrl+A / Ctrl+T | 换画法 / 换配色 |
| Ctrl+O | 叙事偏好：篇幅、节奏、恐怖程度与略去的细节 |
| Ctrl+B | 用量与估算花费 |
| Ctrl+K | 查看规则插件或添加家规 |
| Ctrl+G / Ctrl+Y | 整页打开「经过 / 进展 / 背景」 / 现在整理进展 |
| Esc | 菜单，包括存读档、设置、返回标题和退出 |

| 输入 | 作用 |
|---|---|
| `.ra 侦查`、`.rab 恐吓` | 提议使用技能，守秘人仍会按情境决定是否检定与难度；失败的同一次尝试不能随意重掷 |
| `.r 1d6+1`、`.coc`、`.st` | 掷骰 / 掷一组属性 / 查看人物卡 |
| `.en` | 结局后做成长检定，每局一次 |
| `/kp 问题` | 直接对守秘人说，用于提问或澄清；不作为角色发言、不检定、不推进时间。其余输入由守秘人判断是行动还是场外提问 |
| `/retry` | 从中断的阶段继续，保留已结算的结果和 NPC 回答 |

浏览器会占用部分功能键，例如 F5 刷新。可点击底栏、Esc 菜单，或输入 `/suggest`、`/sheet`、`/notes`、`/map`、`/help`。刷新网页后，联机玩家需要重新入座。

### 人物、记录和画面

标题页「角色卡」可以查看自己的调查员、存档里的调查员和模组预设。支持 AI 协助车卡、导入、复制、导出和删除；已有卡片可编辑名字与背景等文字，不能直接修改数值。游戏里的人物卡还记录本局经历。

「继续调查」列出模组、调查员、时间、地点与回合数，可以预览、改名、复制或删除。删除的存档和角色卡放进 `data/games/trash/`。

右栏显示「经过」，按行动列出记录，点一下可以跳到对应叙述。点「经过」的标题或按 Ctrl+G，整页打开经过、进展和背景：「进展」在有新发现时后台整理，两次至少隔六轮，有新内容时标题上写「进展·新」；「背景」保留开场时知道的事。手稿按种类排版：信件、日记与病历、报纸与告示、电报、典籍各有纸色和格式。地图只画已知地点和道路，可展开内部地点；选中地点按 G，会在输入框里写好前往那里的行动。

默认用块字符画显示照片，Ctrl+A 可切换轮廓、六分块和纯 ASCII 等画法。六分块需要字体支持。终端窄于 96 列时，右栏自动隐藏；不支持真彩的终端会降为 256 色。使用支持真彩和中文等宽显示的终端，画面会更清楚。

即兴配图在后台准备，先显示简图或已有画面，完成后更新。开场、危机、关键地点等时刻可显示整屏「大场景」，按键、点击或八秒后收起，掷骰时让路；图片没准备好就跳过。两种配图都可以在设置中关闭。

「AI 画图」需要本机安装并登录 Codex，按参考图或描述生成，并随剧情重画明显变化的地点和人物。上限是每个模组 100 张、每局 12 张；生成也会消耗相应服务的额度。`doctor` 会显示是否可用，故障处理见游戏内玩法说明的「遇到问题」。

## 战斗与追逐

调查员动手攻击，或者对方因为这次行动动手，守秘人判定打起来以后，游戏进入战斗；模组写定的袭击（有人破窗扑进来）到了那一刻也直接打起来，下一步就选这一轮的行动。进入战斗后，面对的对手占满舞台，背后透着血光，血顺着画面往下流，心跳一样明暗起伏；被它抓中时爪痕划过画面，整个屏幕溅红摇晃。旁边是行动先后、对手的伤势（只写字样，不给数字）和调查员的生命值。每一轮按敏捷从高到低结算，拿着能开火的枪的人先动；每次输入就是这一轮的行动，输入框上方列出可选的攻击、射击、战技、急救、装填、防守和逃跑，也可以自己写（砸油灯、关门这类事交给裁定员）。每项后面写的「被攻击时闪避 / 反击 / 找掩护」是这一轮被打时的应对，Tab 切换。在 Esc 菜单 → 设置里打开「战斗与追逐限时选择」后，选择有时限：战斗 20 秒、追逐 12 秒，时间到了战斗中只防守、追逐中只全力奔跑；帮助、人物卡等窗口打开时暂停，手动输入行动时停止。

规则按第 7 版：闪避与反击、以多打少的奖励骰、射击卡壳和弹药、推倒缴械制住等战技（体格差带来惩罚骰）、极难成功的最大伤害与贯穿、重伤的体质检定、濒死每轮的体质检定。对手、怪物和 AI 队友的行动由「战术」分工安排（模型设置里可以单独换），模型出错时按经验规则行动。怪物没有图片时由 AI 画出它的样子（没有 Codex 时由程序画），第一次看见时要过理智检定。打完以后，被打伤打死的人记进世界记录，模组里因此成立的剧情标记（比如某人被打倒、调查员逃走）随之触发。

逃跑或追赶时进入追逐。开始时每人过一次体质定速度，比所有追兵都快就直接甩开；否则场景变冷变暗、向后掠过，追兵画在里面，离得越近越大，每追近一段就扑近一下；舞台右侧是整条路线：每段的名字、谁在哪一段、跑到哪一段就甩开，以及路上的险阻（⚠，边跑边过，失败会耽搁、可能受伤）和路障（▦，要过检定才能通过）。离追兵两段以上可以躲藏，领先三段或者跑出路线就甩掉了；被追上就打起来。

联机时战斗和追逐的一轮同样是 7 秒的窗口：大家在窗口里各自选行动，最后一个人选完后整轮一起结算，守秘人讲一段。没在窗口里出手的玩家这一轮只防守。

## 守秘人卡组

有《克苏鲁的呼唤守秘人卡组》（好事者、恐惧症、惨事、武器四个 PDF，或装着它们的 zip）时，可以读进游戏：

```bash
autokeeper cards import            # 读源码目录 rule/ 里的卡组（AUTOKEEPER_RULES 可指定别的目录）
autokeeper cards import 卡组.zip   # 也可以写出 zip、PDF 所在目录或单个 PDF
```

每张卡的文字存成结构化数据，卡面存成图片，都在本机缓存的 `cards/` 里，不进 Git。没有导入时游戏照常进行。导入后：

- **恐惧症卡**：调查员陷入不定性疯狂时抽一张恐惧症或躁狂症卡，记进角色卡的「恐惧症和躁狂症」；临时疯狂发作时抽一张发作卡（有同伴在场用即时症状，否则用总结症状；掷到 9、10 改抽恐惧症或躁狂症卡）。卡片弹出显示，可「查看卡面」；守秘人照卡演出，裁定员照卡上的效果给惩罚骰。
- **惨事卡**：孤注一掷再失败、检定失败或过夜休息出了岔子时，裁定员可以选一张。卡上的即时效果（理智检定、损失生命、理智、魔法，幸运减半）由引擎结算，卡片弹出显示，持续的效果之后仍交给裁定员。
- **好事者卡**：导演即兴加人物时可以用一张卡做底子，人物得到卡上的属性、技能与攻击。
- **武器卡**：作为武器数据的补充，供规则查询（`cards.weapon_in`）。

这份卡组的汉化版没有卡图，卡面图片是整张卡的文字面。

## 联机

同一局域网里，房主从标题页「开房间」选模组，安排玩家、AI 或不上场的座位；不够就「加一个空座位」。也可以选择联机存档继续。准备房间里有要发给朋友的网址，朋友在浏览器里打开、填名字、挑座位。房主自己也在准备房间入座。

坐下后按 R「选角色」，可用自己的卡、模组预设，或新建、导入。车卡调用房主的模型，朋友不用配置密钥；用终端加入时，车好的卡也会存进自己电脑的「我的角色」。按 Y「准备好了」，全员准备后开局；「现在开始」会把空座交给 AI。开局后只能回到现有座位，不能更换角色；续档沿用原来的调查员。

### 一轮怎么进行

每位玩家各提交一件事，先后不限；此外还有一个共用的额外行动位，谁先提交谁占用。输入 `/undo` 撤回，空着输入框按回车表示说完了。第一条行动提交后，其他人有 7 秒可以一起行动，7 秒到或大家都说完就结算，之后提交的进入下一轮，不会有人一直等某个人。这一轮的行动视为同时发生：守秘人按提交顺序逐个裁定、检定，AI 同伴也会行动；时间按其中最长的一件计算，不是相加；全部结算后守秘人把这一轮合成一段讲出来。

守秘人讲的时候提交的行动排到下一轮最前面。全角括号开头是场外聊天，例如 `（我去倒杯水）`，不算行动。掉线后由 AI 暂时接手，重新连上并回到原座位即可继续。

房主的名字后有「房主」标记，可以 F9 换模型，当前一段讲完后生效。浏览器加入的人是普通玩家。每回合自动存档，游戏和模型调用都在房主电脑上；未发现的线索、NPC 秘密和模组真相不会发给玩家。

离开准备房间不会关房；标题页「回到房间」能返回。要关闭，请在准备房间或游戏的 Esc 菜单里选「关闭房间」，或退出程序。

### 命令行开房间

```bash
autokeeper host
autokeeper host mymod --seats 玩家,玩家,AI
autokeeper join 192.168.1.5
```

默认模组是《追书人》，第一位调查员留给玩家，其余交给 AI。开房间的终端不占座，房主自己另开终端 `autokeeper join 127.0.0.1`。如果程序显示了不同端口，请连显示的完整地址。续档用 `host --load 存档名`。

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
autokeeper import 模组.pdf --id mymod
autokeeper import 模组.pdf --model codex:default --vision-model codex:default
autokeeper generate "1931 年上海，一张没有标签的唱片" --id changpian
autokeeper autoplay mymod --party 2
```

`--model` 和 `--vision-model` 分别选择导入与读图模型。导入中途断了（断网、额度用完、关了窗口），同一个文件再导一次会接着做：原文不再重新提取，导入 Agent 先读自己上次记的笔记和写了一半的草稿，接着往下写；加 `--fresh` 从头来。导入结束会查找 Wikimedia Commons 上有授权的照片，转成字符画；加 `--no-photos` 跳过，之后可用 `autokeeper art fetch 模组目录` 补图。模组原图和手稿尽量保留；`generate` 从设定生成原创模组，`autoplay` 让 AI 调查员自动游玩，标题页「观战」也能看。

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

打起来或开始追逐时，守秘人用 `start_fight` 或 `start_chase` 开始，每一轮用 `battle_round` 下达调查员的行动；对手的行动、骰子与伤害由引擎按规则结算，和终端里一样，对手只显示伤势字样。详见[战斗与追逐](combat.md#在-claude-code--codex-里)。

## 常见问题

**想严格按模组玩。** 标题页「设置」把自由度改成「严格按模组已有内容」，或启动时加 `--improv 严格`；配置文件也支持 `improv = "严格"`。

**动画结束后没动静，或回复中断。** 先看输入框和状态提示，确认是否在等掷骰或选择。模型请求失败时用 `/retry`，或菜单里的「接着讲」。如果仍然卡住，保留报错和 `data/logs/` 中相关记录，提 issue 前删掉密钥与私人内容。

**原图怎么看？** 手稿发现后可在窗口中读完整文字、看字符画预览，点击「查看原图」；F2 可以重看。尚未发现的内容不会提前解锁。

**数据在哪里？** 装好的 `autokeeper` 见上面的[东西放在哪](#东西放在哪)；从源码目录运行时，仓库的 `data/games/` 是存档，`data/cards/` 是自己的调查员，`data/logs/` 是运行记录，导入的模组在 `modules/local/`；设置都在 `~/.config/autokeeper/`。备份时连同导入的模组一起保存。缓存包括嵌入模型、知识库、预处理图片和大场景图，可以重建，但重新下载、建库或生成图片可能需要联网和额外额度；有想保留的生成图，清理前先备份。详见[文件位置](harness.md#文件放在哪里)。

## 命令索引

以下命令均接在 `autokeeper` 后（从源码运行时是 `uv run autokeeper`）；参数细节可用 `--help` 查看。

| 命令 | 用途 |
|---|---|
| `play` / `serve` | 终端 / 浏览器游玩 |
| `host [模组] [--seats 玩家,AI]` / `join 地址` | 开房间 / 加入房间 |
| `import FILE --id ID [--pages 8-19]` | 导入模组，可指定页码 |
| `generate "灵感" --id ID` / `research DIR` | 生成模组 / 联网核实考据 |
| `check 模组` / `compile 模组` | 校验模组 / 编译 Skill 包（模组写 id 或目录，如 `zhuishuren`） |
| `index DIR` / `ask DIR "问题"` | 建原文知识库 / 提问 |
| `art prepare DIR` | 给缺图的地点、人物、物件和怪物配图：先找照片，没有就让 AI 画，存在本机缓存 |
| `art fetch DIR` / `art color DIR` / `art show DIR NAME` | 补照片 / 字符画上色 / 预览 |
| `showcase 模组` | 提前准备大场景图 |
| `autoplay DIR --party N` | AI 调查员自动跑团 |
| `plugin list DIR` / `plugin test DIR` | 查看规则插件 / 运行插件测试 |
| `cards import [路径]` | 读入守秘人卡组（好事者、恐惧症、惨事、武器） |
| `setup` / `doctor [--ping]` | 准备环境 / 检查环境（可实测连接） |
| `migrate [源码目录]` | 把以前从源码目录玩的存档复制过来 |
| `login [deepseek\|claude\|codex] [--key KEY]` | 选模型、存 API Key |
| `trace` | 查看模型用量与花费 |
| `eval context` / `eval rhetoric` / `eval lore` / `eval style` | 运行评测 |
| `mcp` | 运行 MCP 服务 |

`--preset`、`--model`、`--fallback`、`--search`、`--budget`、`--improv` 等全局选项放在子命令前。例如 `autokeeper --preset deepseek import 模组.pdf`。导入子命令还可以用自己的 `--model` 覆盖本次导入的选择。
