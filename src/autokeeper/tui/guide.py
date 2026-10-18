"""The beginner's guide (新手指南), from the title menu and from F1 in a game: what a
session here is and how to act, the screen, checks and dice, pushing and luck, sanity,
clues and handouts, the keeper's improvising, how it is told (preferences, telling it
again), saving and models, the ending and the debrief, investigators, playing with
friends, dice-bot commands, and the compact key reference (screens.HELP).

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

from . import tips
from .screens import HELP


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    text: str  # Markdown; empty for the key reference


PAGES = [
    Page("play", "怎么玩", """
## 跑团是什么

跑团就是几个人一起讲一个故事。一个人当守秘人，描述你看见什么、听见什么，扮演你碰到的每一个人；其他人各扮演一名调查员，说自己要做什么。后面会发生什么，看你们怎么做，也看骰子。

在这里，守秘人由AI来当。《克苏鲁的呼唤》讲的是普通人撞上了说不清的东西：调查员不是英雄，很多东西打不过，查清真相、活着回来就很好了。

## 说你要做什么

在最下面的输入框里打一句中文，回车：

- 我翻开柜台上的账本，一页一页看
- 我把油灯举高，看看墙上那幅画
- 我问酒馆老板，昨晚有没有生人来过
- 我去警察局

说得具体些更好，“我搜房间”不如“我翻床底和衣柜”。想去别处就说“我去……”。跟人说话也一样，问什么、怎么问，直接说；NPC只知道自己那一份，会撒谎，也会守口如瓶。

实在没头绪，按F8，调查员会照眼下的情况想三件能做的事，挑一件填进输入框，发不发由你。

不用背规则。要不要掷骰由守秘人决定，需要的时候会弹出一个框，写明掷什么、要掷到多少，你按回车掷就行。

不懂规则也可以直接问，比如“孤注一掷是什么意思”，守秘人答一两句，故事不往前走。守秘人讲着的时候，可以先把下一句打好，等他讲完再按回车。

第一次碰到掷骰、线索这些事，故事里会多一行左边带竖线的提示，说一句怎么回事，每种只说一次。不想看，取消右下角的“新手提示”就行。
"""),
    Page("screen", "屏幕上的东西", """
## 一屏看下来

最上面一行是模组名、游戏里的时间和你在的地方。时间跟着行动往前走，天黑了，画面也跟着暗下来。

上半截是这个地方的画面，天气会动。嫌它晃眼或者卡，按F6关掉动画；Ctrl+A换一种画法，Ctrl+T换配色。

中间是故事：守秘人的叙述、NPC的话、掷骰的结果卡、找到的线索，都按先后记在这里，鼠标滚轮能往回翻。再往下一行写着守秘人这会儿在忙什么。最下面是输入框，按Esc，光标就回到这里。

右边一栏是调查员的状态、骰子和线索本。终端窄于96列时这一栏会收起来，按键照样能用。

## 地图和人物卡

F5是地图，只画你知道的地方：去过的、看见了的、听人说起过的。你在哪会亮出来，每处找到的线索、见过的人也写在图下。

F7是完整的人物卡：属性、技能、背景和随身物品，点一下右边的人物也能打开，有队友时按左右方向键切换。检定成功过的技能会打上勾，结局以后可以拿它们做成长检定。
"""),
    Page("dice", "检定与骰子", """
## 什么时候掷骰

不是每件事都要掷骰。推开一扇没锁的门、走过去、问一句话，说了就算做了。只有结果没准、失败了又有代价的时候，守秘人才会叫你过检定。掷不掷、用哪项技能、多难，都由他看情况定，说得再好听也改不了。

叫你掷骰时会弹出一个框：你要做的事，为什么拿不准，用哪项技能，要掷到多少。按回车或空格，骰子就掷出去了。这一掷躲不过去，掷出来就算数。

## 点数怎么看

掷的是1到100，越小越好。框里写着三条线，以侦查60为例：

| 掷出 | 结果 |
|---|---|
| 01 | 大成功 |
| 02–12 | 极难成功，不超过五分之一 |
| 13–30 | 困难成功，不超过一半 |
| 31–60 | 常规成功 |
| 61–99 | 失败 |
| 100 | 大失败；技能不到50时，96以上都是 |

难一些的事，守秘人会要求“困难”或“极难”，要掷到那条线以下才算过。

掷完，故事里会留下一张结果卡：十位骰和个位骰各是几，三条线各过没过，最后是成功等级。卡片下面带箭头的那行，写着这一掷带来了什么，拿到了哪条线索，或者没有收获。

## 奖励骰和惩罚骰

条件对你有利，比如光线充足、工具趁手，守秘人可能给奖励骰；条件不利就是惩罚骰。多掷一个十位骰，奖励骰取小的那个，惩罚骰取大的那个，结果卡上没用上的那颗会被划掉。

## 不能重掷

同一件事刚失败过，情况又没变，再试一次不会重掷，结果照旧，故事里会写“不重掷”。想再争取，要么在弹出“孤注一掷”时搏一次，要么换个办法、换个人问，或者等找到新的线索再来。
"""),
    Page("push", "孤注一掷与幸运", """
## 没过的时候

检定没过，如果这一掷关系到能不能找到东西，结果卡下面会写“还有一次机会”，接着让你选：孤注一掷、花幸运，或者放弃。

## 孤注一掷

豁出去再试一次：调查员换一种更冒险的做法，比如翻墙没翻过去，干脆踩着木箱硬翻，要掷到的数不变。想好再选：再失败，后果一定会落下来，而且比这次更糟，可能摔伤，也可能惊动了人。推过的检定不能再推，也不能再花幸运。理智检定和幸运检定不能孤注一掷；掷出大失败的话，孤注一掷和花幸运都没有。

## 花幸运

用幸运点把差的点数补上，这次直接算恰好成功。比如图书馆使用50，掷出58，差8点，花8点幸运就过了，选项上会写明幸运从多少变成多少。幸运花掉不会回来，往后靠运气的时候更难，最好留到要紧的时候。

## 放弃

接受失败，换条路走。要紧的发现不会就此断掉：第一次没找到，东西还在原处，换个办法或者再仔细查一次还有机会；又没过，或者孤注一掷也没成，它还是会到手，只是要付出代价，比如多花时间、弄出动静。
"""),
    Page("sanity", "理智与疯狂", """
## 理智

理智（SAN）是调查员的神经还能撑多久，开局等于意志。看见尸体、怪物，或者想明白了不该明白的事，就要过一次理智检定，这一掷由系统替你掷：掷出的数不超过当前理智算过，掉得少；没过就掉得多。模组里写的“0/1D4”，意思是过了不掉，没过掉1到4点。

## 疯狂

一次掉5点以上，还要再过一个智力检定。过了反而更糟：你看懂了自己看见的东西，会临时疯狂一阵。一天里掉的理智超过当天开始时的五分之一，会陷入不定性疯狂。理智掉到0就是永久疯狂，这名调查员就玩不下去了。发疯时守秘人会讲出症状，人物卡上也会记着。

理智越低，画面越不稳，时不时抖一下，这不是终端出了毛病。

理智很难涨回来，有的结局会补一些。平时能做的，就是别去看不该看的东西，虽然调查员很少忍得住。
"""),
    Page("clues", "线索与手稿", """
## 找线索

线索跟着具体的东西走。笼统地说“我调查一下”，只会注意到最显眼的那一样；想找什么，就说具体查哪儿、翻哪样，比如“我翻翻壁炉里没烧完的纸片”“我把那张合影翻过来看看背面”。

要紧的线索不会被卡死。第一次检定没过，东西还在原处，守秘人会让你觉得差一点；换个办法或再仔细查一次还有机会，再没过也会到手，只是要付出代价。

## 线索本

找到的线索会单独框出来，再记进线索本，按F2随时翻看全文。线索之间怎么串起来，没人会替你说破：谁在撒谎，哪两件事对得上，要你自己琢磨。

## 手稿

手稿是拿到手的实物，比如一封信、一张手画的地图。拿到时会单独弹出来，按Esc收起；之后打开F2线索本，点最下面的按钮再看。

## 手绘

有的模组会请你把看到的东西画下来。按F4打开画板，鼠标左键画、右键擦，画完交给守秘人，他会看你画得像不像。没人要求时，也可以画给他看。
"""),
    Page("improv", "守秘人会即兴", """
## 模组没写到的地方

守秘人手里有一本模组，但你不用照着模组走。想做模组没写到的事，只要说得通，他会顺着往下编，添个人物、开段支线都有可能，必要时还会多出一个结局。新内容在后台准备，好了就插进故事里，所以有时你还没开口，故事自己往前走了一段。开局时他还会读你的调查员背景，把你在乎的人和事织进去。

## 乱来有后果

太出格的举动，比如当街开枪、把证物一把火烧掉，他不会说“不行”，会先让它发生，再让你承担后果。卡住太久没有进展，他也会递一条线头过来。

想严格按模组玩，在配置文件~/.config/autokeeper/config.toml里加一行：

```
improv = "严格"
```
"""),
    Page("telling", "讲法与偏好", """
## 叙事偏好

按Ctrl+O，或者在标题画面选“叙事偏好”，可以定守秘人怎么讲：篇幅长短、恐怖浓度、节奏快慢，还能勾上不想看到的内容，比如虫、溺水、幽闭。勾上的事照样发生、照样算数，只是放到镜头外，或者一句带过。这些只改讲法，掷骰、线索和理智照常结算，设好以后每一局都按它来。

## 重讲上一段

刚讲完的那段不合口味，按F9请守秘人再讲一遍，可以挑换个讲法、短一点、更克制、多写细节或者更阴森。骰子、线索和NPC说过的话都不变，只换说法。只能重讲最新的那一段，有人接着行动以后就不行了。
"""),
    Page("saves", "存档与换模型", """
## 存档

每回合结束都会自动存档，Ctrl+S也能手动存一次。下次在标题画面选“继续调查”，挑一个存档接着玩，守秘人会先把前情讲一遍。玩到一半记不清了，按Ctrl+R，让他再回顾一遍。

## 换模型

守秘人和NPC背后是AI模型。嫌慢、嫌贵，或者模型连不上，按F3换一个，也能在“经济”“标准”“高级剧情”之间换。大家的记忆都在存档里，换完接着玩，谁也不会忘事。

"""),
    Page("ending", "结局与复盘", """
## 一局结束

打到结局，守秘人会讲完最后一段，再列出找到的线索和用掉的时间。

这时按F10复盘：真相是什么，漏掉的线索在哪儿、要怎样才拿得到，每个人瞒着什么，没走到的结局又该怎么走。复盘里按E，守秘人会讲一段后日谈，说说你们见过的人和每位调查员后来怎样了。

再打.en，给这局成功过的技能做成长检定，每局一次。输入/quit回到标题画面，Ctrl+Q直接退出。
"""),
    Page("card", "调查员", """
## 选谁去调查

开新调查时先挑模组，再挑调查员。模组自带的调查员挑一个就能开局，第一次玩这样最省事，右边能看到他的背景和技能。

想玩自己的人，选列表最下面那两项。

### AI 协助车卡

说一位公众人物的名字，比如鲁迅、居里夫人，AutoKeeper会上网查这个人、找几张照片给你挑；也可以直接描述你想玩的人，比如“在码头长大、胆子很大的女记者”。它再问你几个问题，按模组的年代做成调查员。做好以后可以一句话提修改，比如“射击加到60”。

### 导入已有的卡

骰娘的.st指令、车卡器导出的表格、PDF或Word人物卡、纸质卡的照片、酒馆的角色卡都认，文件可以直接拖进终端。

做好的卡会留着，下次开团列表里写着“我的卡”。开局以后，人物卡只随检定、受伤和成长变化，不能手改。

## 同伴

挑完调查员，可以独自调查，也可以带一两个AI同伴。他们在你之后行动，每两回合动一次，掷骰不用你按。
"""),
    Page("lan", "和朋友一起玩", """
## 开房间

一个人当房主，在命令行里运行：

```
autokeeper host
```

默认开《追书人》。终端会打印一个地址，比如`http://192.168.1.5:8000`，发给同一个网络里的朋友，用浏览器打开就能入座，什么都不用装，房主自己也这样入座。装了AutoKeeper的朋友，也可以在标题画面选“加入局域网房间”，填上房主的地址。

## 入座和轮流

进了房间挑一个空座，人齐了自动开局，也可以选“现在开始”，空座交给AI。大家按座位轮流行动，轮到你时会提示“轮到你了”，要你掷骰时同样会弹出掷骰的框。全角括号开头的话是场外聊天，比如“（我去倒杯水）”，大家都看得到，不算行动。

有人掉线，AI先替他走，重新连上、坐回原座就能接着玩。游戏和存档都在房主那边，每回合自动存；模型和叙事偏好也按房主的来，手绘暂时用不了。想让守秘人重讲上一段，照样按F9。
"""),
    Page("commands", "骰娘指令", """
## 给习惯骰娘的玩家

输入框也认骰娘指令，用中文输入法打出的“。”开头也行，打到一半按右方向键能补全指令和技能名。

| 指令 | 作用 |
|---|---|
| .ra 侦查 | 想在这里用侦查，守秘人来裁定 |
| .ra 困难聆听 | 请他按困难来，最后还是他定 |
| .rab 侦查 | 请求一颗奖励骰 |
| .rap 侦查 | 请求一颗惩罚骰 |
| .r 1d6+1 | 随手掷骰，只写.r就是1D100 |
| .coc | 掷一组属性，.coc 5掷五组 |
| .st | 看人物卡，游戏中不能改 |
| .en | 成长检定，结局以后才能做，每局一次 |
| .ti / .li | 查疯狂发作表，只看不算 |

前四条是检定，只算请求：它就是你这一回合的行动，对着什么、要不要掷、多难，都由守秘人看场景定，要掷时照样弹出掷骰的框。其余几条随时能用，不占回合。

理智检定由守秘人发起，撞见可怕的东西时系统会替你掷，所以.sc在游戏里用不了。
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
            yield Static("新手指南", id="guide-title")
            with Horizontal(id="guide-body"):
                yield OptionList(*(Option(p.title, id=p.id) for p in PAGES), id="guide-pages")
                with ContentSwitcher(initial=f"guide-{self.first_page}", id="guide-text"):
                    for p in PAGES:
                        with VerticalScroll(id=f"guide-{p.id}", classes="guide-page"):
                            yield Markdown(p.text.strip()) if p.text else Static(HELP)
            with Horizontal(id="guide-foot"):
                yield Static("↑↓ 换一页 · Tab 到正文里滚动 · Esc 关闭", id="guide-help")
                yield Checkbox("新手提示", tips.enabled(), id="guide-tips", compact=True)

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
