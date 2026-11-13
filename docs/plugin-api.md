# AutoKeeper 插件 API v1

插件增加需要持续数值/状态的玩法，例如疾病、体温、交易、食物、追逐、法术。普通闲聊、一次性场景和已有规则不需要新插件。不得改写原作真相或替玩家作决定。保留 ASCII 界面。

编译时用 `write_plugin(content)` 提交完整 JSON；成功会执行测试、保存到 `plugins/<id>/` 并登记 module.yaml 的 `plugins: [id]`。即兴导演用方案中的 `plugins` 提交同一结构。插件数据随本局存档，源码与模组编译包一同保留。修改同名插件必须增加语义版本号，并自行兼容已有 state。

必填：`id`（小写字母数字下划线）、`name`、`description`、`code`、`tests`（至少一个实际运行的测试）。可选 `version: "0.1.0"`、`api_version: 1`、`hooks`、`capabilities`、`actions`、`flags`、`initial`。

`code` 是纯 Python，定义 `handle(event, ctx, api)`，返回 `{"state": {...}, "effects": [...]}`，可加 `status` 公开一行状态。不导入模块，不读写文件或联网；不用 eval/exec/compile/getattr/open、`__xxx__` 私有属性、帧/全局（`gi_frame`、`f_globals` 等）、`str.format`、type/object/super 这类。只能调用允许的容器/算术内建与 api。在独立隔离进程、清空环境中运行，单次有时间、内存、输出大小和检定次数限制，越界或出错会暂停该插件，不覆盖主游戏状态。不是完整 Python/第三方包运行环境。

一次行动跨过多个周期时，可返回 `repeat: true`，宿主先完成这段效果，再以更新后的属性和状态重新调用，最多连续 8 段。下一段的检定会使用上一段扣减后的真实属性；不要把数天的检定在一次输出内用旧属性重复掷完。每段和整个事件分别保存执行记录，恢复时不重复结算。

- `event.hook`：`start`（本插件首次启用）、`turn`（行动和耗时结算后）、`visit`（到达地点）、`action`（守秘人在定案里调用已声明动作，Settlement.plugins）。`event.action`/`event.arguments` 用于 action；turn 带 `action` 的行动摘要字典。
- `ctx.state` 是本插件的 JSON 状态（首次为 initial）；`ctx.turn`、`ctx.minutes`、`ctx.location`、`ctx.flags`、`ctx.clues`、`ctx.investigators`、`ctx.previous_version` 可读。调查员包含 id、characteristics、hp、san、inventory、conditions；不提供密钥、配置或对话记录。
- `api.roll("2D10") -> [3, 8]` 返回每颗骰值；支持 1–20 颗 D1–D999，由房主骰子服务实际掷骰。需要 `dice` 能力。
- `api.check(who, "体质", "常规", bonus=0) -> {passed, value, target, text}` 使用宿主统一检定接口，需要 `dice`。who 为调查员 id，难度可为常规、困难、极难，bonus 为 -2 至 2 的奖惩骰。不能提供自定目标值、来源或受众；插件检定默认不勾技能成长。
- `effects` 是声明效果的列表；每种效果必须在 capabilities 声明。`note`：公开叙事 `text`；`flag`：`key` 已声明剧情标记；`attribute`：`who`、`key` 中文属性名、`amount` 增减；`condition`：`who`、`key` 状态名、`amount: 1` 添加或 `-1` 移除；`damage`：`who`、`amount` 非负伤害；`sanity`：`who`、`success: "0"`、`failure: "1D6"`；`clue`：`key` 当前地点且满足前置条件的已有线索。
- 只输出已发生的事实；私有计数器写 state，不用 note 公开。不要通过 attribute 改写 HP/SAN；用宿主的 damage/sanity 以保留重伤、疯狂等规则。
- `status`（可选）：一行面向玩家的公开状态，例如 `"风寒：第 2 天"`，会显示在玩家的规则插件窗口（Ctrl+K），联机时也同步给所有人。默认不显示；只放可以公开的进度，不要把私有计数直接 status。effects 每次产生公开内容时，会在故事里生成一行以插件名标注的简短提示。
- 插件 actions 用 `[{"name":"buy", "description":"何时可以调用；arguments 的键、类型和范围"}]` 声明，hooks 包含 action；宿主的守秘人会按玩家实际行动调用。函数自行检查参数，不能相信愿望已经达成。

测试结构：`{name, event, state, context, dice, checks, expected_state, expected_effects}`。dice 是 api.roll 依次消耗的预设单骰，checks 是 api.check 依次返回的通过/失败。context 覆盖默认场景；expected_state 按子集断言，expected_effects 按补全默认字段后的完整列表断言。预设骰值必须用完。至少覆盖正常流程与边界/失败，不能写只验证空输出的假测试。完整返回类型见 `_example/plugin-output.schema.json`。

理智效果的合法例子：`{"kind":"sanity","who":"某调查员id","success":"0","failure":"1D6"}`。测试 expected_effects 可写完全相同的对象，也可省略值恰为默认值的 success/failure。

最小例子（计数仅用于自定义练习，不改变原作）：

```python
def handle(event, ctx, api):
    state = dict(ctx["state"])
    state["count"] = state.get("count", 0) + 1
    return {"state": state, "effects": []}
```

对应测试：`{"name":"第一次", "state":{}, "expected_state":{"count":1}, "expected_effects":[]}`。hooks 可以只写 `["turn"]`，capabilities 留空。

## 面板、演出、小游戏与更多玩法

插件不只记数值：还可以在右栏放面板、在场景上或故事里放几秒演出、给玩家出题做小游戏、定时、给物品、提醒守秘人、改 NPC 态度、加斜杠命令。代码照旧只在沙箱里跑；它交出的是数据，由游戏自己画出来。场面需要一个机制、一个小游戏、一段演出或一个进度条时（仪式倒数、调查板、追逐、谜题、降神会、电台频率），鼓励写插件。

面板和演出默认所有玩家都看得见（联机时也同步）；写 `"to": ["调查员 id"]` 就只给这几位看，其他座位、观战者都收不到，用于只有某人听见的低语、某人的幻觉。题目只给作答的人。公开的只放已经发生、可以公开的东西，不写真相、NPC 秘密和还没找到的线索。`ctx.avoid` 是玩家的雷区题材，演出和题目都不碰。小游戏可玩可不玩，不能卡住主线。每样东西都会小字标上插件名。

- 新 hooks：`answer`（玩家回答了你的 prompt：`event.prompt` 是 prompt 的 key，`event.value` 是答案，`event.by` 为 player/skip/timeout/ai，`event.step` 是这一局小游戏第几步）、`timer`（计时到点：`event.key`）、`roll`（一次检定最终结算后：`event.check_id/who/source/purpose/skill/passed/level/value/target/difficulty/bonus/pushed/luck_spent`）、`clue`（找到线索：`event.clue` 线索 id、`event.title`）、`command`（玩家输入了你的斜杠命令：`event.command`、`event.text` 命令后面的话、`event.who`）。订阅了这些事件，tests 里每种至少一项。
- 新能力与效果：`panel`、`show`、`prompt`、`timer`（含 schedule）、`item`、`hint`、`attitude`。
- `ctx.acting` 是正在行动的调查员 id；`ctx.npcs` 是当前地点的 NPC（id、name）。

面板：返回里加 `panels`（最多 2 个）就是此刻的样子；不写表示不变，`[]` 表示收起。`{"key":"ritual","title":"仪式倒数","rows":[...]}`，title 最多 12 字，rows 最多 12 行，每行 `kind` 为：
- `text`：`{"kind":"text","text":"一行字","color":"warning"}`
- `bar`：`{"kind":"bar","text":"理智","value":40,"max":99,"color":"error"}`
- `list`：`{"kind":"list","text":"标题","items":["最多 8 项"]}`
- `table`：`{"kind":"table","cells":[["名字","价钱"],["煤油","2 角"]]}`，第一行是表头，最多 7 行 4 列
- `art`：`{"kind":"art","art":"多行 ASCII，最多 10 行 36 列"}`
颜色用 primary、secondary、accent、warning、error、success、foreground、dim 或 `#rrggbb`。测试可写 `expected_panels`（按顺序、按子集比对）。

演出：`{"kind":"show","shape":"stage","title":"号外","frames":[{"art":"ASCII，最多 16 行 60 列","text":"一句话","color":"warning","ms":1200}]}`。`stage` 盖在场景图上，`inline` 是故事里的一块；最多 12 帧、合计 20 秒；玩家可以点掉，关了动画只看最后一帧；不挡叙述。每个插件每轮最多一段。

题目和小游戏：`{"kind":"prompt","key":"dial1","who":"调查员 id（不写就是正在行动的人）","title":"保险箱","text":"第一个转盘拨到几？","style":"number","low":0,"high":9}`。`style`：`choice`（options 2–8 个按钮，答案是选中的那项）、`text`（最多 80 字）、`number`（low–high 的整数）、`order`（把 options 排成顺序，答案是列表）、`pick`（挑若干项，答案是列表）、`board`（`board` 是最多 8×8 的格子，每格最多 4 字宽，答案是 `[行, 列]`，从 0 数）。可选 `default`（没人答时用；不写就取第一项、low、空字、原顺序、不选、左上角），`timeout` 秒（10–900，默认 180），`ai`（AI 调查员怎么答：`default` 或 `random`）。答案作为新的 `answer` 事件回到同一个 handle，state 照常保存，所以多步小游戏就是 prompt → answer → prompt 的循环。一次输出最多一个 prompt；每人同时只开一道题；一局小游戏最多 30 步；非 answer 事件每轮最多开 2 道题。`by` 不是 player 时多半表示玩家没空，按默认收场就好。

其他效果：
- `timer`：`{"kind":"timer","key":"candle","minutes":30}` 或 `"rounds":3`，到点触发 `timer` 事件；两样都不写表示取消。`schedule`：`{"kind":"schedule","key":"dawn","at":"D2 06:00"}`。
- `item`：`{"kind":"item","who":"调查员 id","key":"铜钥匙","amount":1}`，`-1` 拿走。
- `hint`：`{"kind":"hint","text":"一句话"}`，守秘人这一轮必须用一句话写进叙述，标明来自规则；一次一句，最多 80 字。
- `attitude`：`{"kind":"attitude","npc":"NPC id","amount":1,"text":"为什么"}`，-3…3，记成 NPC 对调查员的态度，NPC 说话时会照着。
- 斜杠命令：manifest 写 `"commands":[{"name":"占卜","description":"一句话说何时用"}]`（最多 3 个，不带斜杠，hooks 要有 command），玩家输入 `/占卜 后面的话` 就收到 `command` 事件；输入框补全、Ctrl+K 和菜单都会列出来。

小例子：降神会。hooks `["command","answer"]`，capabilities `["prompt","show"]`。

```python
def handle(event, ctx, api):
    state = dict(ctx["state"])
    if event["hook"] == "command":
        return {"state": state, "effects": [{"kind": "prompt", "key": "ask", "title": "降神会", "style": "choice",
                "text": "烛火一跳。你想问什么？", "options": ["你是谁", "你在找什么"]}]}
    if event["hook"] == "answer" and event["by"] == "player":
        word = "玛丽" if event["value"] == "你是谁" else "钥匙"
        frames = [{"text": "指针慢慢滑向……", "ms": 900}] + [{"art": letter, "ms": 600} for letter in word]
        return {"state": state, "effects": [{"kind": "show", "shape": "stage", "title": "降神会", "frames": frames}]}
    return {"state": state, "effects": []}
```

对应测试：`{"name":"开场","event":{"hook":"command","command":"降神","text":""},"expected_effects":[{"kind":"prompt","key":"ask","title":"降神会","style":"choice","text":"烛火一跳。你想问什么？","options":["你是谁","你在找什么"]}]}`，再加一项 answer 事件的测试。

`roll` 的 `who` 是实际检定者，可能是队友、NPC 或怪物，不等同于 `ctx.acting`。用 `source` 和 `purpose` 过滤关心的检定；普通行动、事件、战斗、追逐及理智／伤势连带检定均可产生通知。等待幸运或孤注一掷选择时不提前投递最终结果，同一结果只处理一次；插件自身引发的检定保留记录，但不递归触发 roll 钩子。详细类型与提交约定见 `docs/check-interface.md`。

## 改核心规则、记数值、给守秘人的暗账

家规与别的百分骰体系要改的往往是核心判定本身，不只是结算之后追加效果。插件可以回答核心规则的判定：manifest 写 `rules: ["sanity_effect"]`（一个判定只能有一个启用的插件回答，两个同名是错误）。每次游戏要做这个判定时，宿主先算出核心规则的答案，再以 `{"hook":"rule","rule":"判定名","input":{...},"core":{...}}` 调用你的 `handle`：`input` 是判定的输入，`core` 是核心规则对这个输入的答案。返回里加 `rule`，写你要改的字段（没写的保持核心的值）；不写 `rule` 表示核心的答案照旧。同一次返回里的 `state` 照常保存。

可回答的判定与字段（等级是整数：0 大失败、1 失败、2 常规、3 困难、4 极难、5 大成功）：
- `roll_level`：入 value（算数的 d100）、target、label、who、skill；出 level。
- `check_terms`：入 target、difficulty（常规/困难/极难）、bonus（奖惩骰数）、who、skill；出 target、bonus、needed、thresholds。
- `can_push`：入 skill、passed、pushed、level、who；出 can_push。
- `luck_needed`：入同上加 value、target、difficulty、luck；出 need（转成功要花的点数，不能为 null）、affordable。
- `sanity_effect`：入 who、loss、kind（模组、守秘人或插件在写这次理智损失时说的“是哪一类事”，如 暴力、无助、非自然，没写是 `""`）、passed（理智检定过了没有：true/false，没有检定的损失是 null）、success_loss、failure_loss（检定过了、没过各损失的骰式）、san、san_start_of_day、san_lost_today、underlying、int_passed、level（理智检定的等级，没有检定时没有这个键）；出 after、permanent、indefinite、int_check、temporary、relapse、bout、duration_dice。需要先掷智力时 `int_check` 为真，带着结果会再问一次。理智检定的 `roll` 事件另带 `sanity_kind`（同上）。
- `wound_assess`、`wound_apply`：入出为伤害与身体的字典；入里的 `weapon` 是打出这一下的武器 `{"name","damage"（伤害骰式）,"impale"（是否贯穿）,"firearm","facts"}`，`facts` 是模组里这一条攻击写的 `facts`（`{键: 数字/文字/真假}`）原样给你；不知道武器的伤害（守秘人裁定、插件自己的伤害）是 `{}`。`derive_sheet`：入 characteristics、age、skills、who；出 hp_max、mp_max、san、san_max、damage_bonus、build、move。`ending_gains`：出 improve。`improvement`：入 who、skill、before、roll、gain_roll；出 improves、gain。
- 答案按核心字段校验：字段不存在、类型不对（`luck_needed.need` 可为 null）整份作废，核心的答案照旧，插件暂停。必须在 1 秒内回答，不能 `api.roll`/`api.check`（要骰子见下）；效果只能是 `track`、`flag`、`hint`、`note`。`derive_sheet` 随每次读人物卡被问，只能由输入决定，不带效果，不能要骰子，宿主按输入缓存。
- 要骰子：规则答案可以不直接答，返回 `"dice": ["1D100", "1D4"]`（至多 6 项，每项是 1–20 颗 D1–D999 的普通骰式，合计至多 20 颗；这时不能同时写 `rule` 或 `effects`）。宿主用本局的骰子掷出（和引擎自己的规则骰一样记入这一步的日志、给玩家看见，中断后重来得到同样的点数），再带着结果调用你一次：`event["dice"]` 是每一项的点数合计，按顺序，`input`、`core` 与第一次相同；第二次必须给最终答案（不能再要骰子，否则整份作废、插件暂停）。只有在结算阶段里才能要骰子；阶段之外（没有日志可记）要骰子被拒绝，核心的答案照旧，插件不暂停。
- tests 里每个 `rules` 里的判定至少一项：`event` 写上面的结构（`core` 必须就是核心规则对 `input` 的答案），`expected_rule` 写你改的字段（`{}` 表示不改）。要骰子的那一次写 `expected_dice`（你要的骰式列表），带结果的那一次在 `event` 里加 `"dice": [点数]`、再写 `expected_rule`。

`rules_text`：一小段 Markdown，写这一局规则与核心规则不同的地方；会放在核心规则之后，标明以这里为准，守秘人和所有读规则的角色都看得到。只写规则本身，不写剧情。

数值：manifest 的 `tracks` 声明记在每个调查员人物卡上的值 `{"key":"corruption","label":"腐化","kind":"pool|number|scores","max":9,"scores":["分项"]}`（pool 在 0 到 max，开局满；scores 要列分项名）。pool 可以写 `"stands_for": "mp"`（`hp`、`mp`、`san`、`luck` 之一）表示人物卡上这一行由它顶替、换成它的名字显示，不写就是在核心的行旁边另起一行。只有本插件的 `track` 效果能写，需要 `track` 能力：`{"kind":"track","who":"调查员 id","key":"corruption","amount":1}`（增减），或 `"value":3`（设成），scores 加 `"name":"分项"`。`ctx.investigators[].tracked` 里能读到。

守秘人的暗账：返回里加 `keeper`（一行，最多 120 字；`""` 清除），只给守秘人看，写在“插件记录”里，不发给任何玩家。适合玩家不该知道的计数。`unreal` 效果（能力 `unreal`）为某位调查员打开或关上幻觉的通道：`{"kind":"unreal","who":"调查员 id","on":true,"text":"几个字的理由"}`。核心规则只在疯狂时允许守秘人给幻觉；打开后即使神志清醒也可以，守秘人会被告知理由；`"on":false` 关上。

最小例子：理智损失加倍，并在人物卡上记腐化。manifest：`rules: ["sanity_effect"]`、`capabilities: ["track"]`、`tracks: [{"key":"corruption","label":"腐化","kind":"number"}]`。

```python
def handle(event, ctx, api):
    state = ctx["state"]
    if event["hook"] != "rule" or event["rule"] != "sanity_effect" or event["core"]["int_check"] or event["input"]["loss"] <= 0:
        return {"state": state, "effects": []}
    i = event["input"]
    return {"state": state, "rule": {"after": max(0, i["san"] - 2 * i["loss"])},
            "effects": [{"kind": "track", "who": i["who"], "key": "corruption", "amount": i["loss"]}]}
```

对应测试：`{"name":"损失加倍","event":{"hook":"rule","rule":"sanity_effect","input":{"who":"x","loss":3,"kind":"","san":60,"san_start_of_day":60,"san_lost_today":0,"underlying":false,"int_passed":null},"core":{"after":57,"permanent":false,"indefinite":false,"int_check":false,"temporary":false,"relapse":false,"bout":false,"duration_dice":""}},"expected_rule":{"after":54},"expected_effects":[{"kind":"track","who":"x","key":"corruption","amount":3}]}`。
