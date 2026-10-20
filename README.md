<div align="center">

<a href="docs/img/title.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/title.png" width="760" alt="AutoKeeper 标题画面：克苏鲁与拉莱耶的彩色字符画"></a>

*Ph'nglui mglw'nafh Cthulhu R'lyeh wgah'nagl fhtagn.*

*我以为，世上最仁慈的事，莫过于人类的心智无法把它所知的一切关联起来。*<br>

<sub><i>H. P. 洛夫克拉夫特，《克苏鲁的呼唤》</i></sub>

<br>

**在终端里玩《克苏鲁的呼唤》第 7 版**

中文跑团 · 彩色 ASCII 演出 · 终端 / 浏览器

[![Release](https://img.shields.io/github/v/release/satoshinji2992/autokeeper?label=version&color=B8860B)](https://github.com/satoshinji2992/autokeeper/releases/latest)
[![CI](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-2EA44F)](LICENSE)

[项目网页](https://satoshinji2992.github.io/autokeeper/) · [游玩体验](#从早班火车进站开始) · [车卡与角色库](#ai-辅助车卡或者把你的老角色带进来) · [模组与插件](#自带三本模组也能导入剧本与规则插件) · [好友联机](#一个人随时开或者叫朋友凑一桌) · [开始玩](#开始玩) · [文档](#文档与开发)

</div>

AutoKeeper 把《克苏鲁的呼唤》第 7 版的跑团桌面搬进了终端。你扮演调查员，用中文说出想做的事；AI 担任守秘人，铺陈场景、扮演镇上的各色人物，并在需要检定时请你掷骰。

- **七版规则与物理十面骰**：检定时亲手投掷 3D 物理十面骰，花幸运、孤注一掷、理智与不定性疯狂、重伤及幕间成长都按规则结算。
- **AI 辅助车卡与多格式导卡**：给一句人设或人物名字即可按七版规则建卡并配好肖像；也支持导入骰娘 `.st`、表格、PDF、纸质卡照片与酒馆卡。
- **彩色 ASCII 视觉演出**：老照片与插图转成真彩字符画，随叙事飘起雨雪浓雾；NPC 开口时立绘登场，开场、遇险与结局展开整屏大图。
- **模组编译与沙箱规则插件**：内置三本模组，支持拖入 PDF、Word、ZIP 模组自动拆解编译；专属机制与解谜小游戏可用规则插件实现，开局后也能一句话加家规。
- **独行、带 AI 队友，或叫朋友上桌**：一个人随时开局；想凑一桌时在标题页开好房间，朋友用浏览器打开链接就能车卡入座，不用装环境或配密钥。

<p align="center">
  <a href="docs/img/game.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/game.png" width="100%" alt="追书人实机：图书馆调查、旧报手稿、物理十面骰，以及右侧的人物状态和调查记录"></a>
  <br><sub>《追书人》实机画面：在阿诺兹堡图书馆查阅旧报、投掷物理十面骰完成检定，右侧同步记录状态与调查经过。</sub>
</p>

## 从早班火车进站开始

挑好模组和调查员，守秘人会顺着你的角色背景拉开序幕。以入门模组《追书人》为例，故事从早班火车驶入阿诺兹堡讲起：委托人托马斯的家在小镇尽头的公墓旁，走过去还有一段路。你可以先向站务员打听方向、找家餐馆吃早饭，或是拐进镇图书馆查查旧报纸。想和谁搭话、翻看什么东西，直接打字就行。如果顺着好奇心走到了原作没写的地方，守秘人会即兴补出人物、地点和支线，并写进本局存档；偏爱原汁原味的话，也能在设置里切回严格模式，或按喜好调整篇幅、节奏与恐怖浓度。

行动撞上悬念时，桌面会弹出检定提示，请你亲手掷出物理模拟的十面骰。失败了要不要花幸运、要不要冒着大祸临头的风险「孤注一掷」，都由你当场拍板。一路上收集的剪报与信件可以随时展开阅读原文、查看原图；去过的地方、打过照面的人和查出的线索，会分别归档进地图、线索本与人物卡的「本局经历」里。每回合自动存档，随时合上终端，下次都能接着往下查。

<p align="center">
  <a href="docs/img/map.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/map.png" width="100%" alt="调查地图：火车站、镇中心、图书馆和金博尔家之间的路，以及各处已知的人物与线索"></a>
  <br><sub>带战争迷雾的调查地图：只画已知地点与道路，标出各处见过的人、找到的线索与当前天气，按 G 即可动身前往。</sub>
</p>

随着调查推进，窗外的天色与天气会跟着叙事流转。老照片与插图转成的彩色字符画上，会实时飘起细雨、大雪、浓雾或摇曳的灯影（也可按 `Ctrl+A` 切换纯 ASCII 等画法）；NPC 说话时，人物立绘会站到画面一侧，随台词里的情绪变换色调甚至微微颤动。

到了开场、撞见异状或步入结局的关头，画面会展开成整屏「大场景」插图。本机装有 Codex 时，开启「AI 画图」还能让即兴人物与地点自动配图；若是剧情里老宅失火、NPC 负伤，场景和头像也会按原图重新绘制。

<p align="center">
  <a href="docs/img/showcase.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/showcase.png" width="100%" alt="追书人开场的大场景：阿诺兹堡早班车，车站照片转成整屏彩色字符画"></a>
  <br><sub>关键时刻的整屏大场景：《追书人》开场，早班火车停靠阿诺兹堡车站。</sub>
</p>

## AI 辅助车卡，或者把你的老角色带进来

开局前可以直接选用模组预设调查员，也能在「角色卡」里亲手建一张属于自己的卡：

- **AI 辅助车卡**：输入一句角色设想（如“落魄的禁酒令私家侦探”），或直接填一位历史、公众人物的名字。写名字时，程序会检索维基百科与历史图库，由你挑选满意的照片转成字符画肖像；接着结合人物经历与模组年代向你提出三个背景问题。答完后，程序按七版规则自动核算属性分配、职业点公式与兴趣点，生成合规的人物卡，不满意的地方还能用一句话继续微调。
- **多格式导入老卡**：手头已有的调查员不用重填。骰娘的 `.st` 属性串由代码直接解析；Excel 表格、PDF、Word 乃至拍下来的纸质人物卡照片，会照原样识别录入并自动修正越界数值；酒馆角色卡（PNG / JSON）也能直接读取人设与卡面图片，走完背景问答后转成 CoC 调查员。

<p align="center">
  <a href="docs/img/cards.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/cards.png" width="100%" alt="角色库：自己的角色、存档里的调查员和模组预设，可以带进新的一局"></a>
  <br><sub>角色库：集中管理自己建的角色、存档里的历任调查员与模组预设，随时带进新的一局。</sub>
</p>

## 自带三本模组，也能导入剧本与规则插件

游戏自带三本风格各异的模组：适合单人上手的古典调查《追书人》、暴风雪孤立小镇中的《冬雪之下》，以及适合多人热闹开局的《有些人油盐不进》。装好后不用额外找剧本，直接就能开第一局。

<p align="center">
  <a href="docs/img/module.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/module.png" width="100%" alt="模组选择界面：冬雪之下的故事介绍、调查人数与彩色字符画预览"></a>
  <br><sub>模组选择界面：自带《追书人》《冬雪之下》《有些人油盐不进》，第一次玩推荐从《追书人》开始。</sub>
</p>

想跑自己攒下的模组，把 PDF、Word、Markdown、纯文本或 ZIP 压缩包拖进标题页的「导入模组」，或者用一行命令导入：

```bash
autokeeper import 模组.pdf
```

导入时，AI 会通读原文，把零散的人物、地点、线索和事件整理成结构化数据，保留原书的插图与手稿，并按年代去公共图库搜寻合适的历史老照片转成字符画，校验通过后直接出现在游戏列表中。手上没有现成剧本时，也能给一句设定，让它联网考据、编写剧情并配好插图，生成一部原创模组：

```bash
autokeeper generate "1931 年上海，一张没有标签的唱片" --id changpian
```

### 规则插件与一句话家规

CoC 模组常有标准规则之外的特殊机制——比如《冬雪之下》的传染病蔓延、《追书人》书房里的旧书架机关，或是防线耐久、倒计时与资源消耗。AutoKeeper 用一套隔离沙箱中的[规则插件](docs/plugins.md)来承载这些设计：

- **模组专属机制与小游戏**：导入或编写模组时可附带 Python 规则插件。插件在屏蔽了文件、网络与子进程的沙箱中运行，能在右侧边栏挂载实时状态面板、播放字符演出、注册自定义 `/命令`，或是向玩家弹出**选择、填空、排序、棋盘**四类交互式解谜小游戏。
- **游戏中一句话加家规**：跑团途中临时起意，随时按 `Ctrl+K` 打开插件窗口，用自然语言输入一条家规（例如“每次在暴风雪里赶路都要检定体质”）。AI 会在后台生成插件并在沙箱中跑通自动化测试，测试通过后立即装进本局生效。

<p align="center">
  <a href="docs/img/plugins.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/plugins.png" width="100%" alt="规则插件：查看本局特殊机制、可用行动，或者用一句话添加家规"></a>
  <br><sub>规则插件与家规面板（Ctrl+K）：查看本局生效的特殊规则、触发状态与可用行动，或用一句话现场添加家规。</sub>
</p>

更多细节见[导入与生成说明](docs/playing.md#导入生成与观战)、[模组格式文档](docs/module-format.md)与[规则插件文档](docs/plugins.md)。

## 一个人随时开，或者叫朋友凑一桌

一个人玩时，可以独自调查，也可以挑几位性格各异的 AI 调查员当队友；单纯想看故事的话，还有「观战」模式能让 AI 小队自己跑完整局。

想约朋友一起跑，由房主在标题页选择「开房间」，排好真人与 AI 的座位，把生成的网址发给朋友就行。朋友用浏览器打开链接就能入座，不需要安装 Python、也不用配置任何模型密钥。每个人都可以用预设卡、导入自己的老角色，或者当场调用房主的模型协助车一张新卡。

<p align="center">
  <a href="docs/img/lobby.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/lobby.png" width="100%" alt="冬雪之下开房间界面：安排真人与 AI 座位，查看调查员，开启浏览器加入"></a>
  <br><sub>《冬雪之下》准备房间：分配真人与 AI 座位、查看队友人物卡，复制一行地址就能叫朋友进房。</sub>
</p>

开局后，同一轮里大家各自提交行动，先后不限，守秘人按顺序逐一裁定、往下推进故事；守秘人讲述时也能提前打字排进下一轮。中途掉线重连即可坐回原位，局域网与虚拟局域网（如 Radmin、ZeroTier）下的连接排查见[联机说明](docs/playing.md#联机)。

<details>
<summary><b>更多实机画面：人物卡详情、存档管理、场景与 NPC 肖像（点击展开）</b></summary>
<br>

每张图均可点击查看高清原图。

### 人物卡与本局经历

<a href="docs/img/sheet.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/sheet.png" width="100%" alt="人物卡：调查员的背景、属性与完整技能列表"></a>

### 存档管理与进度预览

<a href="docs/img/saves.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/saves.png" width="100%" alt="存档管理：模组、调查员、游戏时间和地点，以及存档预览"></a>

### 场景字符画

<a href="docs/img/scenes.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/scenes.png" width="100%" alt="追书人的场景字符画：火车站、金博尔家、书房、大街、公墓和陵墓"></a>

### 各模组 NPC 肖像

<a href="docs/img/portraits.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/portraits.png" width="100%" alt="三本模组中的 NPC 头像，以老照片和画作转成彩色字符画"></a>

</details>

## 开始玩

一条命令装好（需要 [uv](https://docs.astral.sh/uv/getting-started/installation/)；用 pipx 的话是 `pipx install autokeeper`）：

```bash
uv tool install autokeeper
```

然后在任意目录输入 `autokeeper` 开始。第一次运行会装好掷骰组件，再问你用哪家模型、填 API Key。

还没装 uv，可以用一键脚本，它先装 uv，再做上面的事：

```bash
# Linux、macOS、WSL
curl -fsSL https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.sh | bash
```

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
```

```bash
autokeeper serve      # 在浏览器里玩
autokeeper login      # 换模型、补 API Key
uv tool upgrade autokeeper   # 更新
```

掷骰要用 Node.js 18+，推荐再装 [chafa](https://hpjansson.org/chafa/) 显示照片字符画（各系统的装法、存档放在哪、以前从源码玩的存档怎么带过来，见[安装](docs/playing.md#安装)）。

除了 Claude 和 DeepSeek，游戏还支持 Codex、Ollama 本地离线模型以及任意 OpenAI 兼容接口，可以在标题页「设置」或游戏中按 `F9` 随时切换，也能按职责混搭模型并设置预算上限。详细配法与花费说明见[游玩指南](docs/playing.md#模型与花费)。

仓库本身也是一个 **Claude Code 插件**与 **MCP 服务端**——如果你想直接在 Claude Code 或 Codex 对话框里调用它的骰子、模组 Skills 与状态引擎来跑团，可参考[在 Claude Code / Codex 中主持](docs/playing.md#在-claude-code--codex-中主持)。

<details>
<summary><b>0.0.3 更新亮点</b></summary>
<br>

- **会自己运转的世界**：每局有一份世界记录，记着谁在哪、谁知道什么、谁答应过谁；重要 NPC 照自己的打算行事，说了就做。
- **守秘人统一主持**：守秘人看得到全部底牌，每轮先安排玩家的行动和在场人物的主动参与，讲完的场面先记进世界再显示。
- **检定更像真的跑团**：失败就是失败，孤注一掷先说后果；日常物件可以当场拿、当场借。
- **一条命令安装**：`uv tool install autokeeper`，在任意目录启动；以前的存档自动带过来。AutoKeeper 本体不绑定模型，Claude 是可选组件。
- 详见[完整更新记录](CHANGELOG.md#0032026-10-20)。

</details>

<details>
<summary><b>0.0.2 更新亮点与升级须知</b></summary>
<br>

- **3D 物理十面骰**：点数由物理模拟实时投出，自带骨白、黑曜石、血珀、黄铜等六种皮肤。
- **联机大厅与自选角色**：标题页直接开房、入座、车卡或带卡进房，新增行动池与提前输入队列。
- **整屏大场景与动态重绘**：开场、危机、结局展开整屏插图；支持通过本机 Codex 按剧情变故重绘场景与肖像。
- **界面与插件重做**：全新二维迷雾地图、角色库管理、回收站存档管理；支持一句话添加沙箱家规与解谜小游戏。
- **升级提醒**：从 0.0.1 升级前请先备份 `saves/`。0.0.2 将数据目录整理为 `data/`，旧版存档不再兼容续读，但你的调查员角色卡、本地模型与知识库会自动迁移。详见[完整更新记录](CHANGELOG.md#0022026-10-19)。

</details>

## 文档与开发

- [游玩指南](docs/playing.md)：安装、模型配置、快捷键、联机排查、MCP 与 Claude Code 插件用法。
- [模组格式](docs/module-format.md) / [规则插件](docs/plugins.md)：手工编写剧本、配置大场景与开发自定义机制。
- [守秘人体验设计](docs/keeper-experience.md)：开场引入、导演即兴、线索节奏与配图设计。
- [模型运行机制](docs/harness.md) / [评测记录](docs/eval.md) / [世界引擎设计](docs/world.md)：多智能体架构、修辞防注入评测与后续计划。

遇到不合理的裁定或报错，欢迎提交 [Issue](https://github.com/satoshinji2992/autokeeper/issues) 并附上模组名、当轮行动与日志。源码位于 [`src/autokeeper/`](src/autokeeper/)，测试位于 [`tests/`](tests/)；提交 PR 前请运行 `uv run ruff check src tests` 与 `uv run pytest -q`。开发约定详见 [AGENTS.md](AGENTS.md)。

## 致谢与许可

终端界面基于 [Textual](https://github.com/Textualize/textual) 构建，照片转字符画使用 [chafa](https://hpjansson.org/chafa/)，掷骰与骰娘指令使用 [BCDice](https://github.com/bcdice/BCDice)，智能体编排与原文检索分别基于 [LangGraph](https://github.com/langchain-ai/langgraph)、[LangChain](https://github.com/langchain-ai/langchain) 与 [LightRAG](https://github.com/HKUDS/LightRAG)。

本项目代码以 [MIT](LICENSE) 协议发布，[`src/autokeeper/module/example/`](src/autokeeper/module/example/) 为 CC0 格式样例。自带模组《追书人》（改编自 Chaosium 出版、Bret Kramer 著 *Paper Chase*）、《冬雪之下》与《有些人油盐不进》均为同人改编，原作版权归各自原作者与出版方所有，仓库不含原模组文本与原版插图；模组选用的历史照片均来自 Wikimedia Commons，署名与授权见各模组的 `art/credits.yaml`（[追书人](modules/zhuishuren/art/credits.yaml)、[冬雪之下](modules/dongxuezhixia/art/credits.yaml)、[有些人油盐不进](modules/fallout3-youxierenyouyanbujin/art/credits.yaml)）。标题画面背景取自 Dominique Signoret 的画作 *Cthulhu and R'lyeh*（[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)，[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Cthulhu_and_R%27lyeh.jpg)）。Call of Cthulhu 是 Chaosium Inc. 的注册商标，本项目与 Chaosium 无关。
