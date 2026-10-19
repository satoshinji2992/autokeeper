"""The playing guide (玩法说明), from the title menu and from F1 in a game: what a
session here is and how to act, the screen, checks and dice, pushing and luck, sanity,
clues and handouts, the keeper's improvising, how it is told (preferences, telling it
again), saving and models, the ending and the debrief, investigators, playing with
friends, dice-bot commands, what to do when something does not work (AI pictures, big
scenes), and the compact key reference (screens.HELP).

Every page says what the player sees and decides. Its examples come from no module, the
tutorial's least of all: they must not tell a new player what to look for (test_guide.py)."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Checkbox, ContentSwitcher, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from . import tips, trouble
from .screens import HELP


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    text: str  # Markdown; empty for the key reference


PAGES = [
    Page("play", "怎么玩", """
## 先从一句行动开始

你扮演调查员，AI 担任守秘人。玩法采用《克苏鲁的呼唤》第 7 版规则。

读完开场，在底部输入你想做的事，按回车：

- 我翻开柜台上的账本，一页一页看
- 我把油灯举高，看看墙上那幅画
- 我问酒馆老板，昨晚有没有生人来过
- 我去警察局

动作尽量具体，比如“我翻床底和衣柜”。说话、问路、吃饭、歇脚都可以，不必一直查案。别人说的话也未必可信。

## 需要时再查规则

需要检定时，弹窗会写明技能、难度和目标值，按回车或空格掷骰。也可以直接问“孤注一掷是什么意思”，这类规则提问不推进故事。

没头绪时按 **F5** 看三个行动建议。选中后只会填进输入框，你可以修改，也可以不发送。

守秘人叙述时可以先打字并回车：这一段讲完就自动交上，/undo 撤回。操作提示每种只出现一次，可在下方关闭。
"""),
    Page("screen", "界面与按键", """
## 界面

顶部显示模组、游戏时间和当前位置。赶路、搜索和休息会花时间，普通问候可以不计时。

场景以彩色 ASCII 显示。天气动画在 **Ctrl+P** 菜单里开关，**Ctrl+A** 换画法，**Ctrl+T** 换配色。

中间按顺序记录叙述、对话、骰子和线索，滚轮可往回翻。底部输入行动；按 **Esc** 可把光标移回输入框。

右栏显示人物状态、骰子和线索。下面三页按 **Ctrl+G** 或点页签切换：

- 经过：每一步行动、检定和找到的东西，点一行跳到故事里那一段
- 进展：故事至今，有新进展时自动整理，只写你们看到过的事；**Ctrl+Y** 现在整理，每轮一次
- 背景：开场时知道的事，和你的调查员是谁

窗口窄于 96 列时右栏隐藏，仍可用按键打开对应页面；三页会盖在故事上打开。

## 地图和人物卡

**F3** 打开地图，上面是去过和听说过的地方。用方向键选一个地方，下面会写你在那里看到的、见过的人和找到的线索。

名字后面有 ▸ 的地方，里面还有别处，按回车展开。按 G 会在输入框里写好「我去…」，再按回车才出发。地图太大时，可以用鼠标拖动。

**F4** 或点击右栏人物打开人物卡：属性、技能、背景故事和随身物品，还有本局经历：做过的事、找到的线索、受的伤和理智损失、答应过的事。有队友时按左右方向键切换。成功使用过的技能会打勾，结局后可做成长检定。
"""),
    Page("dice", "检定与骰子", """
## 什么时候掷骰

结果不确定，而且失败有实际后果时，守秘人才要求检定。开一扇没锁的门、普通问候通常不用掷骰。

弹窗写明行动、检定理由、技能和目标值。按 **回车或空格** 掷骰，结果随后记入故事。

## 点数怎么看

掷出 1–100，点数越低越好。以侦查 60 为例：

| 掷出 | 结果 |
|---|---|
| 01 | 大成功 |
| 02–12 | 极难成功，不超过五分之一 |
| 13–30 | 困难成功，不超过一半 |
| 31–60 | 常规成功 |
| 61–99 | 失败 |
| 100 | 大失败；技能不到50时，96以上都是 |

难一些的事，守秘人会要求“困难”或“极难”，要掷到那条线以下才算过。

结果卡显示骰值、成功等级和各难度是否通过。下方 **↳** 一行说明这次检定的收获或结果。

## 奖励骰和惩罚骰

有利条件可能获得奖励骰，不利条件可能带来惩罚骰。每颗多掷一个十位骰，奖励取较小的结果，惩罚取较大的结果。没采用的骰子会划掉。

## 不能重掷

同一尝试刚失败、情况又没变化时，继续沿用原结果。可以改变做法、换人询问，或在允许时选择“孤注一掷”。
"""),
    Page("push", "孤注一掷与幸运", """
## 没过的时候

调查检定失败后，系统会列出本次可用的选择：孤注一掷、花幸运或放弃。有些检定不提供全部选项。

## 孤注一掷

换一种更冒险的做法再试一次，难度不变。再次失败会有更严重的后果，例如摔伤或惊动他人。

孤注一掷只能用一次，之后不能花幸运。理智、幸运检定不能孤注一掷；大失败不能孤注一掷或花幸运。

## 花幸运

花掉幸运点，补足与成功相差的点数。图书馆使用 50、掷出 58，花 8 点即可成功。按钮会显示花费和剩余幸运，由你决定是否值得。

## 放弃

接受本次失败。关键线索第一次失败后仍可寻找；再次失败或孤注一掷失败时，会以多花时间、弄出动静等代价取得。普通线索不一定适用这条补救。
"""),
    Page("sanity", "理智与疯狂", """
## 理智

理智（SAN）表示调查员的精神状态，通常以意志作为初始值。遇到模组规定的恐怖场景时，系统自动检定：骰值不超过当前理智算成功。

损失写成“成功时 / 失败时”。例如 **0/1D4**：成功不扣，失败扣 1–4 点。

## 疯狂

一次损失至少 5 点，还要做智力检定；成功会临时疯狂。一天内累计损失达到当天初始理智的五分之一，会不定性疯狂。理智降至 0 是永久疯狂。

症状会写入人物卡，守秘人据此描述调查员的状态。

低理智时画面可能抖动，可在 Ctrl+P 菜单里关掉天气动画。有些结局会恢复理智。
"""),
    Page("clues", "线索与手稿", """
## 找线索

说明你在查什么，例如“我翻壁炉里没烧完的纸片”“我看看那张合影的背面”。笼统地巡视通常只会注意到最显眼的东西。

关键线索检定失败后仍有补救机会，规则见“孤注一掷与幸运”。

## 线索本

**F2** 查看已发现的线索全文，以及记下的约定和物品往来。线索本只记录发现，不会自动给出案件结论。

## 手稿

信件、地图、病历等资料会单独打开。按 **Esc** 收起，之后可从 F2 重看。资料保留完整文字；有原图时可看 ASCII 预览，或点“查看原图”放大阅读。

## 手绘

**F8** 打开画板，左键画、右键擦，完成后点“交给守秘人”。有些模组会用到手绘，没有要求时也可以提交草图。
"""),
    Page("improv", "模组之外", """
## 模组没写到的地方

你可以问路、投宿、闲聊，也可以提出模组未写的调查办法。允许即兴时，守秘人可补充人物、地点和支线；开局也可能采用调查员背景里的关系。

补充内容可能稍后才出现。它应当符合原作设定，不能替你决定行动、答应交易或改变已经发生的事实。

## 选择自由度

标题页“设置”里可选“严格按模组”或“允许即兴”。严格模式只使用已有内容；即兴模式仍受年代、人物能力和已知事实约束。

风险按你实际做的事处理。拒绝委托、改变路线或暂时休息，本身不该受到惩罚。
"""),
    Page("telling", "讲法与偏好", """
## 叙事偏好

**Ctrl+O** 或“设置 → 叙事偏好”可调整篇幅、恐怖程度和节奏。勾选的内容会在新写的叙述和对话中略去细节，但相关事件和规则照常结算。

模组原有的手稿、事件原文仍原样显示。偏好会保存，之后的调查也会沿用。

## 重讲上一段

**F6** 可重讲最新一段，选择更短、更克制或多写细节等讲法。骰子、线索和人物已经说过的话不变。下一次行动开始后，上一段就不能重讲了。
"""),
    Page("saves", "存档与换模型", """
## 存档

每回合结束自动保存，**Ctrl+S** 也可手动存档。下次从“继续调查”读档，会先回顾前情；游戏中按 **Ctrl+R** 也可回顾。

如果模型响应中断，输入 **/retry** 继续未完成的回合，已完成的检定和对话会保留。

## 换模型

标题页“设置”可选模型；游戏中 **F9** 可切换模型和响应模式，本局记录会保留。**Ctrl+B** 查看调用量和费用估算。

"""),
    Page("ending", "结局与复盘", """
## 一局结束

结局后会显示调查用时和已发现的线索。

**F10** 复盘会公开真相、未发现的线索、人物秘密和其他结局。复盘中按 **E** 可听后日谈。

输入 **.en** 给本局成功使用过的技能做成长检定，每局一次。**/quit** 回标题，**Ctrl+Q** 退出程序。
"""),
    Page("card", "调查员", """
## 选谁去调查

选好模组后，右侧可查看调查员的背景和技能。选择现成的人物即可开始，也可以在列表底部创建或导入人物。

### 创建调查员

输入人物描述，例如“在码头长大的女记者”，回答几个问题后生成人物卡。也可输入公众人物的名字，查询资料和照片后按模组年代改编。草稿出来后还能提修改要求，例如“射击加到 60”。

### 导入已有的卡

支持骰娘的 **.st** 指令、人物表格、PDF、Word、人物卡照片和酒馆角色卡，可粘贴文字或拖入文件。

保存后会出现在“我的卡”。开局后不能手动改卡，属性随检定、伤势、成长和规则效果变化。

## 同伴

可以独自调查，或带一两名 AI 同伴。他们在你之后、每两回合行动一次，自行掷骰。
"""),
    Page("lan", "和朋友一起玩", """
## 开房间

一个人当房主，在标题页选“开房间”，选模组，给每位调查员定好玩家、AI 或不上场，也可以接着玩以前的联机存档。开好后进入准备房间，房主也在里面入座。

准备房间里写着要发给朋友的地址。勾了“同时开网页版”，朋友用浏览器打开它；没勾的话，朋友要装好 AutoKeeper，在标题页“加入房间”里填这个地址。命令行 `autokeeper host` 也能开同样的房间。

离开准备房间时房间还开着，标题页的“回到房间”可以回去。在准备房间点“关闭房间”、游戏里从 Ctrl+P 菜单选，或者退出程序，房间才关。

## 入座和行动

选空座后，人齐自动开始；也可选“现在开始”，空座交给 AI。每一轮大家各说一件要做的事，先后不限，另外空着的一个位置谁先说归谁。说错了输入 /undo 撤回，没别的事就在空输入框里按回车。大家都说完，守秘人按先后一口气讲完；他讲的时候也能先打字，会排到下一轮的最前面。检定仍由你确认掷骰。

全角括号开头的话是场外聊天，例如“（我去倒杯水）”，所有人可见，不占行动。

掉线时 AI 暂代行动，重新连接后可坐回原座。游戏、存档、模型和偏好由房主管理。联机可用 F6 重讲，暂不支持手绘。

## 在浏览器里

F5 会刷新网页，刷新后要重新入座。出主意、人物卡、线索本、地图、帮助分别是 /suggest、/sheet、/notes、/map、/help，底栏和 Ctrl+P 菜单也能点。

朋友进不来、网页打不开，看下一页“联机遇到问题”。
"""),
    Page("lan-help", "联机遇到问题", trouble.text(generic=True)),
    Page("commands", "骰娘指令", """
## 给习惯骰娘的玩家

输入框也认骰娘指令，用中文输入法打出的“。”开头也行，打到一半按右方向键能补全指令和技能名。

| 指令 | 作用 |
|---|---|
| .ra 侦查 | 请求侦查检定 |
| .ra 困难聆听 | 请求困难聆听检定 |
| .rab 侦查 | 请求一颗奖励骰 |
| .rap 侦查 | 请求一颗惩罚骰 |
| .r 1d6+1 | 随手掷骰，只写.r就是1D100 |
| .coc | 掷一组属性，.coc 5掷五组 |
| .st | 看人物卡，游戏中不能改 |
| .en | 成长检定，结局以后才能做，每局一次 |
| .ti / .li | 查疯狂发作表，只看不算 |

前四条是检定请求，算作本回合行动。守秘人根据目标和情境决定是否检定、采用什么难度；需要时会弹出掷骰窗口。其余指令不占行动回合，其中成长检定仅限结局后使用。

理智检定由守秘人发起，撞见可怕的东西时系统会替你掷，所以.sc在游戏里用不了。
"""),
    Page("plugins", "规则插件", """
## 模组的额外规则

有些模组需要疾病、资源或特殊行动等额外规则。插件负责记录和结算，相关结果会出现在人物卡或故事里。

**Ctrl+K** 查看本局插件，可暂停或重新启用；暂停不清空已有状态。标题页“设置”也可整体关闭插件。联机由房主管理。

插件出错时会暂停，游戏可继续。需要这些规则时，先查看插件是否仍在运行。
"""),
    Page("trouble", "遇到问题", """
## AI 画图用不了

AI 画图用的是这台电脑上的 Codex。设置里「AI 画图」是灰的时候，下面那行写着原因：

- **没装 Codex**：先装好 Node.js，在终端运行 `npm i -g @openai/codex`，再运行 `codex login` 登录。
- **Codex 还没登录**：在终端运行 `codex login`。
- **这个版本的 Codex 不能画图**：在终端运行 `codex update`。

弄好以后重新打开设置，就能打开 AI 画图。在终端运行 `autokeeper doctor` 也能看到哪里不对。

一个模组最多用 AI 画 100 张（导入和每一局加在一起），每局最多 12 张；画满了就只用现成的图。

## 大场景没出现

大场景的图要提前画好，一张大约一分钟，开局后在后台一张一张画。到时候还没画好就跳过这一次，不会让你等。想在开局前全部画好，在终端运行 `autokeeper showcase 模组名`。

终端里没装 chafa 时显示不了大图；设置里关掉「大场景」也不会出现。
"""),
    Page("keys", "按键", ""),
]


class GuideScreen(ModalScreen[None]):
    """The pages on the left, the one chosen on the right, the switch for the hints at the foot."""

    CSS_PATH = "guide.tcss"
    BINDINGS = [Binding("escape,f1", "dismiss", "关闭")]

    def __init__(self, page: str | None = None):
        super().__init__()
        ids = [p.id for p in PAGES]
        self.first_page = page if page in ids else ids[0]

    def compose(self) -> ComposeResult:
        with Vertical(id="guide"):
            yield Static("玩法说明", id="guide-title")
            with Horizontal(id="guide-body"):
                yield OptionList(*(Option(p.title, id=p.id) for p in PAGES), id="guide-pages")
                with ContentSwitcher(initial=f"guide-{self.first_page}", id="guide-text"):
                    for p in PAGES:
                        with VerticalScroll(id=f"guide-{p.id}", classes="guide-page"):
                            yield Markdown(p.text.strip()) if p.text else Static(HELP)
            with Horizontal(id="guide-foot"):
                yield Static("↑↓ 换一页 · Tab 到正文里滚动 · Esc 关闭", id="guide-help")
                yield Checkbox("操作提示", tips.enabled(), id="guide-tips", compact=True)

    def on_mount(self) -> None:
        pages = self.query_one("#guide-pages", OptionList)
        pages.highlighted = [p.id for p in PAGES].index(self.first_page)
        pages.focus()

    @on(OptionList.OptionHighlighted, "#guide-pages")
    def turn_to(self, event: OptionList.OptionHighlighted) -> None:
        self.query_one(ContentSwitcher).current = f"guide-{event.option.id}"

    @on(OptionList.OptionSelected, "#guide-pages")
    def read_page(self, event: OptionList.OptionSelected) -> None:
        self.query_one(f"#guide-{event.option.id}").focus()  # Enter: into the text, to scroll it

    @on(Checkbox.Changed, "#guide-tips")
    def switch_tips(self, event: Checkbox.Changed) -> None:
        tips.set_enabled(event.value)
