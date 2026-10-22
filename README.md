<div align="center">

<a href="docs/img/title.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/title.png" width="760" alt="AutoKeeper 标题画面：克苏鲁与拉莱耶的彩色字符画"></a>

*Ph'nglui mglw'nafh Cthulhu R'lyeh wgah'nagl fhtagn.*

*我以为，世上最仁慈的事，莫过于人类的心智无法把它所知的一切关联起来。*<br>

<sub><i>H. P. 洛夫克拉夫特，《克苏鲁的呼唤》</i></sub>

<br>

**在终端里玩《克苏鲁的呼唤》第 7 版**

中文跑团 · 真彩字符画 · 终端 / 浏览器

[![Release](https://img.shields.io/github/v/release/satoshinji2992/autokeeper?label=version&color=B8860B)](https://github.com/satoshinji2992/autokeeper/releases/latest)
[![CI](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/satoshinji2992/autokeeper/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-2EA44F)](LICENSE)

[项目网页](https://satoshinji2992.github.io/autokeeper/) · [游玩体验](#从早班火车进站开始) · [守秘人与世界](#守秘人与世界记录) · [画面与手稿](#画面与手稿) · [角色库](#调查员与角色库) · [模组与插件](#模组导入生成与规则插件) · [战斗与追逐](#战斗与追逐) · [好友联机](#单人游玩与好友联机) · [开始玩](#开始玩) · [模型与记忆](#模型与上下文管理) · [开发者模式](#开发者模式与-ai-排查) · [文档](#文档与开发)

</div>

AutoKeeper 是一款采用《克苏鲁的呼唤》第 7 版规则的中文跑团游戏，可以在终端或浏览器中游玩。你扮演调查员，用中文描述行动、与人物交谈；AI 担任守秘人，介绍场景、扮演 NPC，在需要检定时请你掷骰。

- **守秘人统一主持**：KP 掌握模组底牌，结合上下文理解行动，安排在场 NPC 主动协助、阻拦或提出要求；裁定、人物演出和导演准备各有分工。
- **持久世界记录**：人物位置、同行关系、重要物品、知情范围、承诺、日程和设定改写随存档保存。人物声称的事与实际发生的事分别记录。
- **模组编译与即兴**：内置三本模组，也可导入 PDF、Word、Markdown 或 ZIP。编译期整理人物动机、线索条件、日程、规则与遭遇；游玩时可继续扩展，并保留原作图片和手稿。
- **七版规则与物理十面骰**：亲手投掷 3D 物理十面骰，结算幸运、孤注一掷、灵感、理智、疯狂、重伤与成长；支持导入规则书和守秘人卡组。
- **战斗与追逐**：自由描述行动或使用选单，按七版规则逐轮结算。战斗与追逐可以互相转换，伤势、弹药、武器和沿途障碍的变化持续保留。
- **真彩字符画与纸张手稿**：照片、插图与 AI 配图转成字符画，叠加天色、雨雪、灯影与战斗演出；信件、日记、剪报、电报各有版式。
- **车卡与多人游玩**：支持 AI 辅助建卡、多格式导卡、角色库与 AI 队友。朋友通过浏览器加入房间，无需安装环境或配置模型密钥。
- **沙箱规则插件**：模组特殊机制、解谜交互与临时家规由插件承载，AI 可生成代码，经沙箱测试后加载。
- **模型与上下文管理**：支持 DeepSeek、Claude、Codex、Ollama 及 OpenAI 兼容接口；分工可独立选模型，配合会话压缩、按需检索和预算限制。
- **开发者网页与 AI 排查**：终端继续游玩，网页查看完整调用、后台任务、世界状态与历史记录，也可指定回合让独立 AI 协助分析问题。

当前版本的战斗、追逐与规则系统见 [0.0.4 更新记录](CHANGELOG.md#0042026-10-22)，世界记录与统一主持从 0.0.3 引入。**0.0.4 不读取旧版本存档，原文件会保留。**

<p align="center">
  <a href="docs/img/game.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/game.png" width="100%" alt="追书人实机：在图书馆查旧报，图书馆使用检定的物理十面骰，守秘人讲出旧报上的内容；右侧是人物状态、调查经过和玩家自己的笔记"></a>
  <br><sub>《追书人》实机画面：在阿诺兹堡图书馆查阅旧报、投掷物理十面骰完成检定，右侧同步记录状态与调查经过。</sub>
</p>

## 从早班火车进站开始

选定模组和调查员后，守秘人会结合角色背景安排开场。《追书人》的故事从早班火车驶入阿诺兹堡开始：委托人托马斯的家在小镇尽头的公墓旁，走过去还有一段路。你可以向站务员问路，在餐馆吃过早饭，或先去图书馆查阅旧报。要与谁交谈、查看什么，直接输入即可。探索延伸到原作未写的地方时，守秘人会补充人物、地点与支线，保存在本局中；希望遵循原作，也可以切换严格模式。叙述篇幅、节奏与恐怖程度均可在设置中调整。

需要检定时，你会亲手投掷 3D 物理模拟的十面骰。失败后，可以选择花费幸运，或说明新的尝试方式，承担「孤注一掷」的风险；守秘人据此复核，结算实际耗时与后果。调查陷入僵局时，可按 `Ctrl+L` 请求灵感检定，获得关于遗漏线索的提示。理智损失、临时与不定性疯狂、重伤和幕间成长也有对应的结算。持有规则书与守秘人卡组时，还可导入游戏，供裁定查询原文，并在疯狂与惨事结算时抽卡。

查到的东西由守秘人在叙述里讲出，NPC 知道的事由他们亲口说出，游戏不替玩家把线索整理成卡片或清单：按 F2 打开笔记自己记录，或在输入框输入 `/记 内容` 记下一行。收集到的剪报与信件可以随时在笔记的「资料」页展开，阅读原文、查看原图。地图记录去过和听说过的地点，人物卡保存「本局经历」。每回合自动存档，关闭终端后，下次可从原处继续。

<p align="center">
  <a href="docs/img/paper.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/paper.png" width="100%" alt="道格拉斯·金博尔的日记：米色纸张、红色页边、斜体手写正文"></a>
  <br><sub>手稿按纸张排版：《追书人》里道格拉斯的日记，红色页边、手写斜体，墨迹逐字渐显。</sub>
</p>

<p align="center">
  <a href="docs/img/map.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/map.png" width="100%" alt="冬雪之下的彩绘地图：主街、诊所、教堂、邮局与农场之间的土路，右侧是主街的说明与去各处的路程"></a>
  <br><sub>彩绘调查地图：只画走过和听说过的地方，地面、土路和雾都由程序生成；右侧是地点的所见、遇到的人和到各处的路程，按 G 即可动身。</sub>
</p>

## 守秘人与世界记录

KP 掌握模组的真相、人物秘密与当前世界状态，负责理解你的意图、组织场面和把握节奏。人物的主动性也由这一轮的局势决定：托马斯可以主动带路、递出工具或提出帮忙；有自己打算的人也可能拒绝、隐瞒、撒谎或抢先行动。

| 部分 | 当前职责 |
| --- | --- |
| 守秘人 | 理解玩家行动，安排在场人物参与，提交场面变化并叙述结果 |
| NPC 演出 | 依据自己的所知、动机和关系回应，同时提交台词与当场动作 |
| 裁定员 | 读取整理后的行动与规则资料，决定检定、耗时和结果 |
| 导演 | 准备即兴内容，在长途赶路、过夜等时机检查幕后局势、提出调整 |
| 世界记录与结算 | 保存事实与日程，执行确定的步骤，核对位置、同行和物品等操作 |

每轮的场面变化有独立记录：谁在场、谁跟随调查员、谁拿着什么。程序核对这些操作，遇到冲突就把具体原因交回修正，再显示相应叙述。NPC 说“我陪您去”要落实为同行；说“钥匙不在我这里”则可能是在撒谎，不会因此改变钥匙的实际归属。

不在场的人按日程与触发条件活动，换场景、时间跳过或相关事件发生时，再集中处理需要判断的动向。模组已经写定结果的步骤由程序执行。世界记录保留实际发生的事、谁知道什么、尚未兑现的约定与设定改写；会话摘要用于帮助模型接续故事，具体事实仍可回查。

导演补充的地点、线索、人物与规则保存在本局中，也可以调整幕后设定并留下改写依据。需要的新内容会先准备再进入场面；玩家提前抵达时，界面显示“KP 正在构思…”，等待相关准备完成。**本局的即兴与改写不覆盖导入的模组文件，也不影响其他存档。**

## 画面与手稿

模组中的老照片与插图会转成真彩字符画。天色随时间变化，雨雪、浓雾与摇曳的灯光叠加在场景中；按 `Ctrl+A` 可切换纯 ASCII 等画法。NPC 发言时，肖像出现在画面一侧，色调随情绪变化。信件、日记、剪报、电报与典籍各有纸色、墨色和版式，手写信与日记以斜体呈现，墨迹逐字显现。

模组原有的图片优先使用。缺少配图的地点、人物与重要物件，会从公共图库和网页寻找写实照片，由读图模型筛选、裁剪；没有合适素材且本机装有 Codex 时，再由 AI 绘制。怪物直接生成配图，战斗时登上舞台，追逐时合成进场景。配图统一调色，缓存在本机，供同一模组的不同存档复用，也可用 `autokeeper art prepare 模组` 在开局前准备。临时路人不生成头像；离线且没有 Codex 时，缺图内容以文字呈现。

开场、重大异状与结局可用整屏「大场景」插图呈现。本机装有 Codex 并开启「AI 画图」后，也能为即兴人物和地点生成配图。老宅失火、人物负伤，或伏笔在后续来访中发生变化时，场景与肖像可以依据原图重绘；这些变化图在剧情需要时才生成。

<p align="center">
  <a href="docs/img/showcase.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/showcase.png" width="100%" alt="追书人开场的大场景：阿诺兹堡早班车，车站照片转成整屏彩色字符画"></a>
  <br><sub>关键时刻的整屏大场景：《追书人》开场，早班火车停靠阿诺兹堡车站。</sub>
</p>

## 调查员与角色库

开局可以选用模组预设调查员，也可以新建或导入角色。角色库集中保存自建人物、存档里的调查员和模组预设，供下一局选用。

- **AI 辅助车卡**：输入角色设想，例如“落魄的禁酒令私家侦探”，或一位历史、公众人物的名字。以人物姓名建卡时，程序会检索维基百科与历史图库，供你选择肖像，再结合人物经历和模组年代提出三个背景问题。完成问答后，按七版规则核算属性、职业点与兴趣点，并支持继续用自然语言调整。
- **导入已有角色**：支持骰娘 `.st` 属性串、Excel、PDF、Word 和纸质人物卡照片，识别录入后校正越界数值。酒馆角色卡（PNG / JSON）可保留人设与卡面图片，经背景问答转换为 CoC 调查员。

<p align="center">
  <a href="docs/img/cards.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/cards.png" width="100%" alt="角色库：自己的角色、存档里的调查员和模组预设，可以带进新的一局"></a>
  <br><sub>角色库：集中管理自己建的角色、存档里的历任调查员与模组预设，随时带进新的一局。</sub>
</p>

## 模组导入、生成与规则插件

游戏自带《追书人》《冬雪之下》《有些人油盐不进》三本模组，分别呈现古典调查、暴风雪中的孤立小镇与多人冒险。初次游玩可以从适合单人入门的《追书人》开始。

<p align="center">
  <a href="docs/img/module.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/module.png" width="100%" alt="模组选择界面：冬雪之下的故事介绍、调查人数与彩色字符画预览"></a>
  <br><sub>模组选择界面：自带《追书人》《冬雪之下》《有些人油盐不进》，第一次玩推荐从《追书人》开始。</sub>
</p>

自己的模组也可以导入。将 PDF、Word、Markdown、纯文本或 ZIP 文件拖入标题页的「导入模组」，或使用命令：

```bash
autokeeper import 模组.pdf
```

AI 会读取原文，整理人物、地点、线索和事件，保留原有插图与手稿，并从公共图库补充符合年代的照片。编译与校验通过后，模组即可出现在游戏列表中。导入所用模型可以单独配置。

### 编译期准备什么

编译会整理供 KP 临场使用的材料：人物知道什么、想要什么、与谁有牵连；没人插手时会发生的日程；场景背景、线索门槛、伏笔变化、特殊规则，以及战斗数值和追逐路线。开场引入与见面反应也会提前准备，游玩时可按调查员的背景和实际行动调整。

有依赖的内容分步补写：先整理人物认知与动机，再生成关系和计划，最后对照相关原文复核。例如，“知道叔叔写日记”与“已经找到日记”必须区分；“买到酒”也不等于“已把酒交给守墓人”。原作的检定、前置条件与可能后果会随资料交给主持使用，新增计划和规则一并导出到编译后的参考文件。

已有模组可用 `autokeeper upgrade 模组 --dry-run` 查看缺少哪些准备，这一步不调用模型、不写文件；确认后运行 `autokeeper upgrade 模组` 补全。运行时，KP 根据当前场景和本局记录使用这些材料，必要时查询原文；临场新增的安排继续写进本局记录。

### 生成原创模组

没有现成剧本时，也可以从一句设定开始，让 AI 联网考据、编写剧情并准备插图：

```bash
autokeeper generate "1931 年上海，一张没有标签的唱片" --id changpian
```

### 规则插件与家规

《冬雪之下》的传染病、《追书人》的旧书架机关，以及防线耐久、倒计时、资源消耗等特殊机制，可以由[规则插件](docs/plugins.md)实现。插件既可随模组提供，也可在游玩中生成：

- **模组专属机制**：Python 插件在禁止文件、网络和子进程访问的沙箱中运行，可以显示边栏状态、播放字符演出、注册自定义 `/命令`，并提供选择、填空、排序与棋盘四类交互。
- **临时家规**：开启开发者模式（F12），按 `Ctrl+K` 打开插件窗口，描述要添加的规则，例如“每次在暴风雪里赶路都要检定体质”。AI 生成插件并运行沙箱测试，通过后加载到本局。

<p align="center">
  <a href="docs/img/plugins.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/plugins.png" width="100%" alt="规则插件：查看本局特殊机制、可用行动，或者用一句话添加家规"></a>
  <br><sub>规则插件与家规面板（开发者模式下 Ctrl+K）：查看本局生效的特殊规则、触发状态与可用行动，或用一句话现场添加家规。</sub>
</p>

更多细节见[导入与生成说明](docs/playing.md#导入生成与观战)、[模组格式文档](docs/module-format.md)与[规则插件文档](docs/plugins.md)。

## 战斗与追逐

冲突进入战斗后，行动按第 7 版规则逐轮结算。对手占据舞台，血光与心跳随交锋起伏；扑击时逼近，受伤时全屏溅血、留下爪痕并震动，命中时白光闪烁，显示血色伤害数字。怪物首次出现时，可用全屏插图呈现，再进行理智检定。侧栏保留行动顺序与调查员生命值，对手只显示伤势描述。

你可以选择攻击、射击、推倒、缴械、制住、急救、装填、防守或逃跑，也可以直接描述自己的办法。被攻击时的闪避、反击与扑向掩体单独显示，按 `Tab` 切换，不占用行动。默认在首次被攻击、原定应对不适用或危险升高时询问，窗口说明对应检定与代价；限时选择可在设置中开启，默认关闭。

引擎结算以多打少、射击修正、卡壳、贯穿、重伤、濒死与急救，伤势、弹药和掉落的武器保存在世界记录中。对手依据实际感知行动，不能攻击或追赶已失去踪迹的目标；护甲、自愈等特性在能够观察到时呈现。需要阅读长段叙述时，可按 `Ctrl+N` 收起画面。具体规则覆盖范围与简化见[规则对照表](docs/rules-reference.md)。

<p align="center">
  <a href="docs/img/fight.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/fight.png" width="100%" alt="追书人书房里的战斗：犬脸的佝偻身影占满舞台，旁边是出手先后与伤势，下方是战斗卡片、行动选单和被攻击时的应对"></a>
  <br><sub>《追书人》书房里的战斗：对手只写伤势，每次交锋一张战斗卡；选单下方是被攻击时的应对，按 Tab 切换。</sub>
</p>

逃离或追赶会进入沿地图路线展开的追逐。开始时通过体质检定确定速度，每轮按速度差获得行动点；途中可以翻墙、穿过灌木，或撬开、撞开锁着的门。场景色调转冷，画面随奔跑向后掠过，追兵越近，身影越大；侧栏显示路线、障碍和各人的位置。

甩开视线后，追兵可能跟丢；你也可以尝试躲藏，或回身迎战。被制住、倒在追兵面前时，追逐可以转为战斗；敌人逃离后，同样可以选择追赶。两者共用同一场遭遇，保留伤势、弹药、武器和实际位置。撞开的门、破坏的障碍也会留在世界中，之后经过时仍按改变后的状态处理。

<p align="center">
  <a href="docs/img/chase.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/chase.png" width="100%" alt="墓园里的追逐：调查员追着犬脸的佝偻身影，翻矮墙时摔了一跤；右侧是到出口的路，下方是灌木的几种过法"></a>
  <br><sub>《追书人》墓园里的追逐：右侧是到出口的一条路和路上的障碍，选项写明每种过法用什么检定。</sub>
</p>

## 单人游玩与好友联机

单人游玩可以独自调查，也可以选择 AI 调查员同行。「观战」模式则由 AI 小队进行游戏，供你观看故事发展。

多人游玩由房主在标题页选择「多人游戏 → 开房间」，安排真人与 AI 座位，再将房间网址发给朋友。加入者用浏览器打开链接即可，不需要安装 Python 或配置模型密钥。每个座位可选模组预设、导入已有角色，或使用房主的模型新建调查员。

<p align="center">
  <a href="docs/img/lobby.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/lobby.png" width="100%" alt="冬雪之下开房间界面：左侧是座位与调查员选择，右侧是选中调查员的肖像与人物卡"></a>
  <br><sub>《冬雪之下》开房间：左上排座位（玩家或 AI），左下为座位选调查员，右侧预览人物卡。</sub>
</p>

同一轮中，各位玩家可按任意顺序提交行动，视为同时发生。守秘人依次裁定，再统一叙述本轮经过；讲述期间提交的内容排入下一轮。战斗中，每位玩家自行掷骰、选择应对，限时窗口显示剩余秒数。线索原文与守秘人资料都留在房主端，掉线后可重新连接并回到原座位。终端加入时，笔记只保存在自己的电脑上；浏览器笔记暂只保留在当前页面中，关闭或刷新后会清空。局域网及 Radmin、ZeroTier 等虚拟局域网的连接方法见[联机说明](docs/playing.md#联机)。

队友之间可以直接交谈：以引号或“对某某说”“对大家说”开头的发言，会立即显示给全桌、写入记录，不占行动名额，也不另请 KP 回应；AI 同伴和在场 NPC 能在后续回应中接续。场外聊天使用 `//` 或整句双括号，`（压低声音）别进去。` 这类带动作的台词仍按角色发言处理。单人带 AI 队友时采用同样的区分。

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

掷骰组件需要 Node.js 18+。安装脚本会准备 uv 与 AutoKeeper，并尝试安装用于照片字符画的 chafa；各系统的依赖安装方法见[游玩指南](docs/playing.md#安装)。

```bash
# Linux、macOS、WSL
curl -fsSL https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.sh | bash
```

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
```

安装完成后，在新终端运行 `autokeeper`。首次配置会引导你选择模型、填写所需凭据。也可以使用以下命令：

```bash
autokeeper serve      # 在浏览器里玩
autokeeper login      # 换模型、补 API Key
uv tool upgrade autokeeper   # 更新
```

<details>
<summary><b>手动安装：uv / pipx</b></summary>
<br>

已有 uv 时，可直接安装 AutoKeeper；使用 pipx 则运行 `pipx install autokeeper`。尚未安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，可使用官方脚本：

```bash
# Linux、macOS、WSL
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装 uv 后打开新终端，运行：

```bash
uv tool install autokeeper            # 用 Claude 的话：uv tool install "autokeeper[claude]"
```

首次运行会准备掷骰组件。推荐安装 [chafa](https://hpjansson.org/chafa/) 显示照片字符画；存档位置与各系统的依赖说明见[安装说明](docs/playing.md#安装)。

</details>

## 模型与上下文管理

AutoKeeper 的 harness 负责模型调用、会话、工具执行和追踪。DeepSeek、Claude、Codex、Ollama 及 OpenAI 兼容接口可按分工组合使用。模型可在标题页「设置」或游戏中的 `F9` 菜单切换；「各分工模型」支持为守秘人、NPC、裁定与读图等分别配置模型，留空沿用方案默认值。「测试连接」会逐一检查所用模型，预算限制和调用用量可在设置与开发者记录中查看。

会话与模型后端分开保存，随本局存档接续。近期对话保留在上下文中，较早内容达到窗口限制后由摘要模型压缩；人物态度、已作决定、谎言、约定和未完成事项是摘要的重点。压缩失败会保留原记录供后续重试，世界中的位置、物品、事件和当前设定则由记录库维护。

KP 可按需查询人物、物品、历史事件与模组原文；模组建有 LightRAG 索引时，也可进行关联检索。规则书有独立的本地索引，裁定或提问时可查询相应原文。NPC 演出只接收自己的资料与所知，KP 则读取主持所需的幕后信息。

游玩回合按行动安排、裁定、人物回应和叙述等阶段执行，已完成的回应与结算留有恢复记录。模组导入、原创生成等长任务通过工具循环读取材料、检索资料和写入文件，过程可追踪。Ollama 可用于本地离线运行；具体配置、预算与花费说明见[游玩指南](docs/playing.md#模型与花费)。

<p align="center">
  <a href="docs/img/models.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/models.png" width="100%" alt="模型设置的各分工模型页：守秘人、守秘人安排、裁定、NPC、导演等分工与各自的模型"></a>
  <br><sub>模型设置（F9）的「各分工模型」页：每个分工一行，写明它做什么；只有「读图」需要能看图的模型。</sub>
</p>

## 开发者模式与 AI 排查

游戏中按 `F12`、输入 `/dev`，或在「设置」中开启开发者模式。终端显示本轮调用与场面概况，同时打开本机私有网页，查看完整的后台资料。

网页按存档持续保留玩家输入、模型实际收到的上下文、系统提示与摘要、回复、重试、用量、世界操作和图片任务。可以回查某一轮的人物位置、日程与导演进展，检索或下载记录；分页与会话压缩不会清除已经保存的诊断历史。

遇到剧情、知情范围或状态不一致的问题，可以指定回合、选中记录并提交问题，由独立 AI 对照证据排查。排查会说明使用了哪些资料，不能修改存档、世界或人物记忆；仅查看和下载记录不调用模型。旧版本没有保存过的请求无法补回。

当前源码还支持从标题页开房间的房主使用这一套面板、私有网页与规则插件；其他玩家和观战者没有开发者入口，也不会收到隐藏资料。使用方法见[开发者模式文档](docs/developer-mode.md)。

<p align="center">
  <a href="docs/img/developer.png"><img src="https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/docs/img/developer.png" width="100%" alt="开发者模式：顶栏变色，叙述下方列出人物实际位置与接下来的日程"></a>
  <br><sub>终端中的开发者面板：显示本轮模型调用、人物实际位置与日程；完整历史与 AI 排查在本机私有网页中查看。</sub>
</p>

<details>
<summary><b>0.0.4 更新亮点与升级须知</b></summary>
<br>

- **战斗与追逐重写**：按第 7 版规则一轮一轮结算；选项列在输入框上方，被攻击时的应对按 Tab 切换，危险升高时才询问；追逐沿地图上的路线跑，伤势、弹药和掉落的武器记进世界记录。
- **灵感检定与孤注一掷**：调查陷入僵局时按 Ctrl+L 做灵感检定；孤注一掷要写明怎么做，由裁定员复核，时间照常流逝。
- **守秘人**：针对人的检定按对方的技能定难度；恐怖之物先写看到、听到、闻到的，不说名字；伏笔在之后的来访中逐步变化，场景图随之变样。
- **规则书与卡组**：`autokeeper rules index` 读入规则书，裁定和 `/kp` 提问时查原文；`autokeeper cards import` 读入守秘人卡组，疯狂与惨事照卡抽取。
- **模组**：三本自带模组补上追逐路线、写定的袭击和伏笔的变化；《有些人油盐不进》加上小镇的时钟。
- **联机**：每个座位自己掷骰、自己应对攻击，倒计时按秒计；守秘人的资料不再发给客户端。
- **升级须知**：0.0.4 不兼容旧存档，以前的存档会标为旧版本、拒绝读取，原文件保留；在 Claude Code 里经 MCP 主持的方式与 Claude Code 插件已删除。
- 详见[完整更新记录](CHANGELOG.md)。

</details>

<details>
<summary><b>0.0.3 更新亮点</b></summary>
<br>

- **会自己运转的世界**：每局有一份世界记录，保存人物位置、知情范围、承诺和日程，区分人物打算、口头说法与已经完成的行动。
- **守秘人统一主持**：守秘人看得到全部底牌，每轮先安排玩家的行动和在场人物的主动参与，讲完的场面先记进世界再显示。
- **检定更像真的跑团**：失败就是失败，孤注一掷先说后果；日常物件可以当场拿、当场借。
- **配图**：彩绘地图；手稿按信件、日记、报纸、电报、典籍排版。
- **模型按分工配置**：每个分工可以单独选模型，Claude 方案的裁定改用 Sonnet；提示词缓存命中更高，等待更短。
- **一条命令安装**：`uv tool install autokeeper`，在任意目录启动；从源码目录玩的存档自动带过来。AutoKeeper 本体不绑定模型，Claude 是可选组件。

</details>

## 文档与开发

- [游玩指南](docs/playing.md)：安装、模型配置、快捷键、战斗与追逐、规则书与卡组、联机排查。
- [模组格式](docs/module-format.md) / [规则插件](docs/plugins.md)：手工编写剧本、配置大场景与开发自定义机制。
- [守秘人体验设计](docs/keeper-experience.md)：开场引入、导演即兴、线索节奏与配图设计。
- [模型运行机制](docs/harness.md) / [评测记录](docs/eval.md) / [世界引擎设计](docs/world.md)：模型会话、工具循环、评测与世界记录的设计；历史设计与后续计划在相应章节说明。
- [战斗与追逐](docs/combat.md)：规则怎么分层、一轮怎么结算、追逐的地图、联机只发公开的局面。
- [规则对照表](docs/rules-reference.md)：战斗、伤害、追逐、检定与主持，逐条对照《守秘人规则书》的页码、实现和测试。
- [开发者模式](docs/developer-mode.md)：终端概况、本机完整记录网页、历史查询与独立 AI 排查。

遇到不合理的裁定或报错，欢迎提交 [Issue](https://github.com/satoshinji2992/autokeeper/issues) 并附上模组名、当轮行动与日志。源码位于 [`src/autokeeper/`](src/autokeeper/)，测试位于 [`tests/`](tests/)；提交 PR 前请运行 `uv run ruff check src tests` 与 `uv run pytest -q`。开发约定详见 [AGENTS.md](AGENTS.md)。README 的截图由 [`dev/screenshots.py`](dev/screenshots.py) 生成。

## 致谢与许可

终端界面基于 [Textual](https://github.com/Textualize/textual) 构建，照片转字符画使用 [chafa](https://hpjansson.org/chafa/)，掷骰与骰娘指令使用 [BCDice](https://github.com/bcdice/BCDice)，智能体编排与原文检索分别基于 [LangGraph](https://github.com/langchain-ai/langgraph)、[LangChain](https://github.com/langchain-ai/langchain) 与 [LightRAG](https://github.com/HKUDS/LightRAG)。

本项目代码以 [MIT](LICENSE) 协议发布，[`src/autokeeper/module/example/`](src/autokeeper/module/example/) 为 CC0 格式样例。自带模组《追书人》（改编自 Chaosium 出版、Bret Kramer 著 *Paper Chase*）、《冬雪之下》与《有些人油盐不进》均为同人改编，原作版权归各自原作者与出版方所有，仓库不含原模组文本与原版插图；模组选用的历史照片均来自 Wikimedia Commons，署名与授权见各模组的 `art/credits.yaml`（[追书人](modules/zhuishuren/art/credits.yaml)、[冬雪之下](modules/dongxuezhixia/art/credits.yaml)、[有些人油盐不进](modules/fallout3-youxierenyouyanbujin/art/credits.yaml)）。标题画面背景取自 Dominique Signoret 的画作 *Cthulhu and R'lyeh*（[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)，[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Cthulhu_and_R%27lyeh.jpg)）。Call of Cthulhu 是 Chaosium Inc. 的注册商标，本项目与 Chaosium 无关。
