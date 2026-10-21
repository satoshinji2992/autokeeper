<div align="center">

<a href="docs/img/title.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/title.png" width="760" alt="AutoKeeper 标题画面：克苏鲁与拉莱耶的彩色字符画"></a>

*Ph'nglui mglw'nafh Cthulhu R'lyeh wgah'nagl fhtagn.*

*我以为，世上最仁慈的事，莫过于人类的心智无法把它所知的一切关联起来。*<br>

<sub><i>H. P. 洛夫克拉夫特，《克苏鲁的呼唤》</i></sub>

<br>

**在终端里玩《克苏鲁的呼唤》第 7 版**

中文跑团 · 照片转成的字符画 · 终端 / 浏览器

[![Release](https://img.shields.io/github/v/release/satoshinji2992/autokeeper?label=version&color=B8860B)](https://github.com/satoshinji2992/autokeeper/releases/latest)
[![CI](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-2EA44F)](LICENSE)

[项目网页](https://satoshinji2992.github.io/autokeeper/) · [游玩体验](#从早班火车进站开始) · [画面](#照片画字符没有照片就现场画) · [车卡与角色库](#ai-辅助车卡或者把你的老角色带进来) · [模组与插件](#自带三本模组也能导入剧本与规则插件) · [战斗与追逐](#打起来或者拼命跑) · [好友联机](#一个人随时开或者叫朋友凑一桌) · [开始玩](#开始玩) · [文档](#文档与开发)

</div>

AutoKeeper 把《克苏鲁的呼唤》第 7 版的跑团桌面搬进了终端。你扮演调查员，用中文说出想做的事；AI 担任守秘人，铺陈场景、扮演镇上的各色人物，并在需要检定时请你掷骰。

- **会自己运转的小镇**：守秘人看得到模组的全部底牌，每局有一份世界记录，记着谁在哪、知道什么、答应过谁；NPC 照自己的打算行事，你不去找他们，他们也在走自己的日程。
- **七版规则与物理十面骰**：检定时亲手投掷 3D 物理十面骰；失败就是失败，花幸运、孤注一掷、理智与不定性疯狂、重伤和幕间成长都按规则结算。
- **战斗与追逐**：按第 7 版一轮一轮结算。对手占满舞台，受伤时全屏溅血，每轮的选择可以限时；追逐时追兵在场景里越逼越近。对手和怪物的行动由守秘人的战术分工安排，没有图的怪物由 AI 画好放上舞台。
- **字符画演出**：老照片与插图转成真彩字符画，随时间和叙事飘起雨雪浓雾、摇曳灯影；没有照片的地点、人物和物件由程序现场画出，地图是一张彩绘的羊皮纸。
- **按纸张排版的手稿**：信件、日记、剪报、电报、典籍各有纸色、墨色与版式，手写的信和日记用斜体，墨迹逐字渐显。
- **AI 辅助车卡与多格式导卡**：一句人设或一个人名即可按七版规则建卡；也能导入骰娘 `.st`、表格、PDF、纸质卡照片与酒馆卡。
- **模组编译与沙箱规则插件**：内置三本模组，支持拖入 PDF、Word、ZIP 自动拆解编译；专属机制与解谜小游戏用规则插件实现，开局后也能一句话加家规。
- **独行、带 AI 队友，或叫朋友上桌**：一个人随时开局；想凑一桌时开好房间，朋友用浏览器打开链接就能入座，不用装环境或配密钥。
- **不绑定模型**：DeepSeek、Claude、Codex、Ollama 与任意 OpenAI 兼容接口都能用，守秘人、NPC、裁定、读图等分工可以各配一个模型，并设预算上限。

<p align="center">
  <a href="docs/img/game.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/game.png" width="100%" alt="追书人实机：图书馆调查、旧报手稿、物理十面骰，以及右侧的人物状态和调查记录"></a>
  <br><sub>《追书人》实机画面：在阿诺兹堡图书馆查阅旧报、投掷物理十面骰完成检定，右侧同步记录状态与调查经过。</sub>
</p>

## 从早班火车进站开始

挑好模组和调查员，守秘人会顺着你的角色背景拉开序幕。以入门模组《追书人》为例，故事从早班火车驶入阿诺兹堡讲起：委托人托马斯的家在小镇尽头的公墓旁，走过去还有一段路。你可以先向站务员打听方向、找家餐馆吃早饭，或是拐进镇图书馆查查旧报纸。想和谁搭话、翻看什么东西，直接打字就行。如果顺着好奇心走到了原作没写的地方，守秘人会即兴补出人物、地点和支线，并写进本局存档；偏爱原汁原味的话，也能在设置里切回严格模式，或按喜好调整篇幅、节奏与恐怖浓度。

行动撞上悬念时，桌面会弹出检定提示，请你亲手掷出物理模拟的十面骰。失败了要不要花幸运、要不要冒着大祸临头的风险「孤注一掷」，都由你当场拍板。一路上收集的剪报与信件可以随时展开阅读原文、查看原图；去过的地方、打过照面的人和查出的线索，会分别归档进地图、线索本与人物卡的「本局经历」里。每回合自动存档，随时合上终端，下次都能接着往下查。

<p align="center">
  <a href="docs/img/paper.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/paper.png" width="100%" alt="道格拉斯·金博尔的日记：米色纸张、红色页边、斜体手写正文"></a>
  <br><sub>手稿按纸张排版：《追书人》里道格拉斯的日记，红色页边、手写斜体，墨迹逐字渐显。</sub>
</p>

<p align="center">
  <a href="docs/img/map.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/map.png" width="100%" alt="冬雪之下的彩绘地图：主街、诊所、教堂、邮局与农场之间的土路，右侧是主街的说明与去各处的路程"></a>
  <br><sub>彩绘调查地图：只画走过和听说过的地方，地面、土路和雾都由程序生成；右侧是地点的所见、遇到的人、找到的线索和到各处的路程，按 G 即可动身。</sub>
</p>

## 照片画字符，没有照片就找、就画

模组自带的老照片与插图会转成彩色字符画，窗外的天色和天气跟着叙事流转：细雨、大雪、浓雾，夜里的台灯会摇曳（也可按 `Ctrl+A` 切换纯 ASCII 等画法）。NPC 开口时，人物立绘站到画面一侧，色调随台词里的情绪变化。

模组没有配图的地点、人物、重要物件和怪物，以及守秘人即兴加出来的，先上网找写实照片，由读图模型挑选、裁剪；找不到合适的，本机有 Codex 时由 AI 按写实照片的样子画一张。两者都统一轻度调色，放在一起不突兀。图存在本机，每个模组只找、只画一次，以后每局都用；`autokeeper art prepare 模组` 可以在开局前全部准备好。路人不配头像；离线又没有 Codex 时不配图，只用文字。

到了开场、撞见异状或步入结局的关头，画面会展开成整屏「大场景」插图。本机装有 Codex 时，开启「AI 画图」还能让即兴人物与地点改由图像模型配图；若是剧情里老宅失火、NPC 负伤，场景和头像也会按原图重新绘制。

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

## 打起来，或者拼命跑

撞上怪物或者谈崩了，守秘人判定打起来以后，游戏进入按第 7 版规则一轮一轮结算的战斗。面对的对手占满舞台，背后透着血光，血顺着画面往下流，心跳一样明暗起伏；它扑过来时贴近画面，被它抓中时爪痕划过画面、整个屏幕溅红摇晃；被你打中时它白光一闪，伤害数字用血写在它身上。第一次看见怪物时，它先占满整个屏幕，再过理智检定。旁边是出手先后、对手的伤势（只写字样，不给数字）和你们的生命值。

每一轮从列出的行动里选，也可以自己写：攻击、射击、推倒缴械制住、急救、装填、防守、逃跑。选择有时限（战斗 20 秒、追逐 12 秒，设置里可以关掉），时间到了只来得及防守或逃跑。闪避与反击、以多打少、卡壳、贯穿、重伤和濒死都按规则来。对手、怪物和 AI 队友怎么出手，由守秘人的战术分工安排。

<p align="center">
  <a href="docs/img/fight.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/fight.png" width="100%" alt="与食尸鬼道格拉斯的战斗：食尸鬼占满舞台，旁边是出手先后与伤势，下方是战斗卡片和限时的行动选单"></a>
  <br><sub>《追书人》书房里的战斗：对手只写伤势，每次交锋一张战斗卡，行动在输入框上方限时选择。</sub>
</p>

逃跑或追赶时进入追逐：先过体质定速度。场景变冷变暗、向后掠过，追兵就站在里面，离你越近画得越大，每追近一段就猛地扑近一下；旁边是整条路线：每一段的名字、要跳要爬要撞开的东西、谁在哪一段、跑到哪里就能甩开。离得够远可以躲起来，被追上就打起来。

<p align="center">
  <a href="docs/img/chase.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/chase.png" width="100%" alt="墓园里的追逐：食尸鬼站在暗下来的墓园里，旁边是还差的段数和前方的铁栅栏"></a>
  <br><sub>墓园里的追逐：追兵离得越近画得越大，▦ 是要翻过去的路障。</sub>
</p>

## 一个人随时开，或者叫朋友凑一桌

一个人玩时，可以独自调查，也可以挑几位性格各异的 AI 调查员当队友；单纯想看故事的话，还有「观战」模式能让 AI 小队自己跑完整局。

想约朋友一起跑，由房主在标题页选择「开房间」，排好真人与 AI 的座位，把生成的网址发给朋友就行。朋友用浏览器打开链接就能入座，不需要安装 Python、也不用配置任何模型密钥。座位只定是玩家还是 AI、谁来坐；调查员在座位下方另选，可以用模组预设、导入自己的老角色，或者当场调用房主的模型车一张新卡。

<p align="center">
  <a href="docs/img/lobby.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/lobby.png" width="100%" alt="冬雪之下开房间界面：左侧是座位与调查员选择，右侧是选中调查员的肖像与人物卡"></a>
  <br><sub>《冬雪之下》开房间：左上排座位（玩家或 AI），左下为座位选调查员，右侧预览人物卡。</sub>
</p>

开局后，同一轮里大家各自提交行动，先后不限，视为同时发生：守秘人逐个裁定检定，再把这一轮合成一段讲出来；守秘人讲述时也能提前打字排进下一轮。中途掉线重连即可坐回原位，局域网与虚拟局域网（如 Radmin、ZeroTier）下的连接排查见[联机说明](docs/playing.md#联机)。

<details>
<summary><b>更多实机画面：电报、人物卡、存档管理、场景与 NPC 肖像（点击展开）</b></summary>
<br>

每张图均可点击查看高清原图。

### 电报

<a href="docs/img/telegram.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/telegram.png" width="100%" alt="冬雪之下的军队电报：黄色电报纸上贴着一条条打字纸条"></a>

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

先装 [uv](https://docs.astral.sh/uv/getting-started/installation/)（Python 工具管理器，已经有了就跳过）：

```bash
# Linux、macOS、WSL
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

装好后开一个新终端，一条命令装好 AutoKeeper（用 pipx 的话是 `pipx install autokeeper`）：

```bash
uv tool install autokeeper            # 用 Claude 的话：uv tool install "autokeeper[claude]"
```

然后在任意目录输入 `autokeeper` 开始。第一次运行会装好掷骰组件，再问你用哪家模型、填 API Key。

也可以用一键脚本，它会先装 uv、chafa，再做上面的事：

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

除了 Claude 和 DeepSeek，游戏还支持 Codex、Ollama 本地离线模型以及任意 OpenAI 兼容接口，可以在标题页「设置」或游戏中按 `F9` 随时切换。「各分工模型」页可以给守秘人、NPC、裁定、读图等分工各填一个模型，留空就用方案默认；「测试连接」会把用到的每个模型都测一遍。详细配法与花费说明见[游玩指南](docs/playing.md#模型与花费)。

<p align="center">
  <a href="docs/img/models.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/models.png" width="100%" alt="模型设置的各分工模型页：守秘人、守秘人安排、裁定、NPC、导演等分工与各自的模型"></a>
  <br><sub>模型设置（F9）的「各分工模型」页：每个分工一行，写明它做什么；只有「读图」需要能看图的模型。</sub>
</p>

<details>
<summary><b>0.0.3 更新亮点</b></summary>
<br>

- **会自己运转的世界**：每局有一份世界记录，记着谁在哪、谁知道什么、谁答应过谁；重要 NPC 照自己的打算行事，说了就做。
- **守秘人统一主持**：守秘人看得到全部底牌，每轮先安排玩家的行动和在场人物的主动参与，讲完的场面先记进世界再显示。
- **检定更像真的跑团**：失败就是失败，孤注一掷先说后果；日常物件可以当场拿、当场借。
- **配图**：彩绘地图；没有图片的地点、人物、物件和怪物先找写实照片，再由 AI 画；手稿按信件、日记、报纸、电报、典籍排版。
- **模型按分工配置**：每个分工可以单独选模型，Claude 方案的裁定改用 Sonnet；提示词缓存命中更高，等待更短。
- **一条命令安装**：`uv tool install autokeeper`，在任意目录启动；以前的存档自动带过来。AutoKeeper 本体不绑定模型，Claude 是可选组件。
- 详见[完整更新记录](CHANGELOG.md)。

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

- [游玩指南](docs/playing.md)：安装、模型配置、快捷键、联机排查。
- [模组格式](docs/module-format.md) / [规则插件](docs/plugins.md)：手工编写剧本、配置大场景与开发自定义机制。
- [守秘人体验设计](docs/keeper-experience.md)：开场引入、导演即兴、线索节奏与配图设计。
- [模型运行机制](docs/harness.md) / [评测记录](docs/eval.md) / [世界引擎设计](docs/world.md)：多智能体架构、修辞防注入评测与后续计划。
- [战斗与追逐](docs/combat.md)：规则怎么分层、一轮怎么结算、战术分工怎么接、画面怎么做。
- [开发者模式](docs/developer-mode.md)：游戏中按 `F12`，查看本轮每次调用的分工、模型、用时与缓存命中，所有人物的实际位置和接下来的日程。

遇到不合理的裁定或报错，欢迎提交 [Issue](https://github.com/satoshinji2992/autokeeper/issues) 并附上模组名、当轮行动与日志。源码位于 [`src/autokeeper/`](src/autokeeper/)，测试位于 [`tests/`](tests/)；提交 PR 前请运行 `uv run ruff check src tests` 与 `uv run pytest -q`。开发约定详见 [AGENTS.md](AGENTS.md)。README 的截图由 [`dev/screenshots.py`](dev/screenshots.py) 生成。

<p align="center">
  <a href="docs/img/developer.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/developer.png" width="100%" alt="开发者模式：顶栏变色，叙述下方列出人物实际位置与接下来的日程"></a>
  <br><sub>开发者模式（F12）：顶栏变色，叙述下方显示本轮的模型调用、所有人物的实际位置和接下来的日程。</sub>
</p>

## 致谢与许可

终端界面基于 [Textual](https://github.com/Textualize/textual) 构建，照片转字符画使用 [chafa](https://hpjansson.org/chafa/)，掷骰与骰娘指令使用 [BCDice](https://github.com/bcdice/BCDice)，智能体编排与原文检索分别基于 [LangGraph](https://github.com/langchain-ai/langgraph)、[LangChain](https://github.com/langchain-ai/langchain) 与 [LightRAG](https://github.com/HKUDS/LightRAG)。

本项目代码以 [MIT](LICENSE) 协议发布，[`src/autokeeper/module/example/`](src/autokeeper/module/example/) 为 CC0 格式样例。自带模组《追书人》（改编自 Chaosium 出版、Bret Kramer 著 *Paper Chase*）、《冬雪之下》与《有些人油盐不进》均为同人改编，原作版权归各自原作者与出版方所有，仓库不含原模组文本与原版插图；模组选用的历史照片均来自 Wikimedia Commons，署名与授权见各模组的 `art/credits.yaml`（[追书人](modules/zhuishuren/art/credits.yaml)、[冬雪之下](modules/dongxuezhixia/art/credits.yaml)、[有些人油盐不进](modules/fallout3-youxierenyouyanbujin/art/credits.yaml)）。标题画面背景取自 Dominique Signoret 的画作 *Cthulhu and R'lyeh*（[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)，[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Cthulhu_and_R%27lyeh.jpg)）。Call of Cthulhu 是 Chaosium Inc. 的注册商标，本项目与 Chaosium 无关。
