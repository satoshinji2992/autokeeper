# AutoKeeper 插件 API v1

插件增加需要持续数值/状态的玩法，例如疾病、体温、交易、食物、追逐、法术。普通闲聊、一次性场景和已有规则不需要新插件。不得改写原作真相或替玩家作决定。保留 ASCII 界面。

编译时用 `write_plugin(content)` 提交完整 JSON；成功会执行测试、保存到 `plugins/<id>/` 并登记 module.yaml 的 `plugins: [id]`。即兴导演用方案中的 `plugins` 提交同一结构。插件数据随本局存档，源码与模组编译包一同保留。修改同名插件必须增加语义版本号，并自行兼容已有 state。

必填：`id`（小写字母数字下划线）、`name`、`description`、`code`、`tests`（至少一个实际运行的测试）。可选 `version: "0.1.0"`、`api_version: 1`、`hooks`、`capabilities`、`actions`、`flags`、`initial`。

`code` 是纯 Python，定义 `handle(event, ctx, api)`，返回 `{"state": {...}, "effects": [...]}`，可加 `status` 公开一行状态。不导入模块，不读写文件或联网；不用 eval/exec/compile/getattr/open、`__xxx__` 私有属性、帧/全局（`gi_frame`、`f_globals` 等）、`str.format`、type/object/super 这类。只能调用允许的容器/算术内建与 api。在独立隔离进程、清空环境中运行，单次有时间、内存、输出大小和检定次数限制，越界或出错会暂停该插件，不覆盖主游戏状态。不是完整 Python/第三方包运行环境。

一次行动跨过多个周期时，可返回 `repeat: true`，宿主先完成这段效果，再以更新后的属性和状态重新调用，最多连续 8 段。下一段的检定会使用上一段扣减后的真实属性；不要把数天的检定在一次输出内用旧属性重复掷完。每段和整个事件分别保存执行记录，恢复时不重复结算。

- `event.hook`：`start`（本插件首次启用）、`turn`（行动和耗时结算后）、`visit`（到达地点）、`action`（裁定员调用已声明动作）。`event.action`/`event.arguments` 用于 action；turn 带 `action` 的行动摘要字典。
- `ctx.state` 是本插件的 JSON 状态（首次为 initial）；`ctx.turn`、`ctx.minutes`、`ctx.location`、`ctx.flags`、`ctx.clues`、`ctx.investigators`、`ctx.previous_version` 可读。调查员包含 id、characteristics、hp、san、inventory、conditions；不提供密钥、配置或对话记录。
- `api.roll("2D10") -> [3, 8]` 返回每颗骰值；支持 1–20 颗 D1–D999，由房主骰子服务实际掷骰。需要 `dice` 能力。
- `api.check(who, "体质", "常规", bonus=0) -> {passed, value, target, text}` 使用宿主统一检定接口，需要 `dice`。who 为调查员 id，难度可为常规、困难、极难，bonus 为 -2 至 2 的奖惩骰。不能提供自定目标值、来源或受众；插件检定默认不勾技能成长。
- `effects` 是声明效果的列表；每种效果必须在 capabilities 声明。`note`：公开叙事 `text`；`flag`：`key` 已声明剧情标记；`attribute`：`who`、`key` 中文属性名、`amount` 增减；`condition`：`who`、`key` 状态名、`amount: 1` 添加或 `-1` 移除；`damage`：`who`、`amount` 非负伤害；`sanity`：`who`、`success: "0"`、`failure: "1D6"`；`clue`：`key` 当前地点且满足前置条件的已有线索。
- 只输出已发生的事实；私有计数器写 state，不用 note 公开。不要通过 attribute 改写 HP/SAN；用宿主的 damage/sanity 以保留重伤、疯狂等规则。
- `status`（可选）：一行面向玩家的公开状态，例如 `"风寒：第 2 天"`，会显示在玩家的规则插件窗口（Ctrl+K），联机时也同步给所有人。默认不显示；只放可以公开的进度，不要把私有计数直接 status。effects 每次产生公开内容时，会在故事里生成一行以插件名标注的简短提示。
- 插件 actions 用 `[{"name":"buy", "description":"何时可以调用；arguments 的键、类型和范围"}]` 声明，hooks 包含 action；宿主裁定员会按玩家实际行动调用。函数自行检查参数，不能相信愿望已经达成。

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
