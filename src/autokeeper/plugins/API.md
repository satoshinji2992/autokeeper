# AutoKeeper 插件 API v1

插件增加需要持续数值/状态的玩法，例如疾病、体温、交易、食物、追逐、法术。普通闲聊、一次性场景和已有规则不需要新插件。不得改写原作真相或替玩家作决定。保留 ASCII 界面。

编译时用 `write_plugin(content)` 提交完整 JSON；成功会执行测试、保存到 `plugins/<id>/` 并登记 module.yaml 的 `plugins: [id]`。即兴导演用方案中的 `plugins` 提交同一结构。插件数据随本局存档，源码与模组编译包一同保留。修改同名插件必须增加语义版本号，并自行兼容已有 state。

必填：`id`（小写字母数字下划线）、`name`、`description`、`code`、`tests`（至少一个实际运行的测试）。可选 `version: "0.1.0"`、`api_version: 1`、`hooks`、`capabilities`、`actions`、`flags`、`initial`。

`code` 是纯 Python，定义 `handle(event, ctx, api)`，返回 `{"state": {...}, "effects": [...]}`。不导入模块，不读写文件或联网。只能调用普通容器/算术内建及 api。单次有时间、内存、输出大小和检定次数限制，错误会暂停该插件，不覆盖主游戏状态。不是完整 Python/第三方包运行环境。

一次行动跨过多个周期时，可返回 `repeat: true`，宿主先完成这段效果，再以更新后的属性和状态重新调用，最多连续 8 段。下一段的检定会使用上一段扣减后的真实属性；不要把数天的检定在一次输出内用旧属性重复掷完。每段和整个事件分别保存执行记录，恢复时不重复结算。

- `event.hook`：`start`（本插件首次启用）、`turn`（行动和耗时结算后）、`visit`（到达地点）、`action`（裁定员调用已声明动作）。`event.action`/`event.arguments` 用于 action；turn 带 `action` 的行动摘要字典。
- `ctx.state` 是本插件的 JSON 状态（首次为 initial）；`ctx.turn`、`ctx.minutes`、`ctx.location`、`ctx.flags`、`ctx.clues`、`ctx.investigators`、`ctx.previous_version` 可读。调查员包含 id、characteristics、hp、san、inventory、conditions；不提供密钥、配置或对话记录。
- `api.roll("2D10") -> [3, 8]` 返回每颗骰值；支持 1–20 颗 D1–D999，由房主骰子服务实际掷骰。需要 `dice` 能力。
- `api.check(who, "体质", "常规") -> {passed, value, target, text}` 由房主 CoC 规则裁定，需要 `dice`。难度可为常规、困难、极难。
- `effects` 是声明效果的列表；每种效果必须在 capabilities 声明。`note`：公开叙事 `text`；`flag`：`key` 已声明剧情标记；`attribute`：`who`、`key` 中文属性名、`amount` 增减；`condition`：`who`、`key` 状态名、`amount: 1` 添加或 `-1` 移除；`damage`：`who`、`amount` 非负伤害；`sanity`：`who`、`success: "0"`、`failure: "1D6"`；`clue`：`key` 当前地点且满足前置条件的已有线索。
- 只输出已发生的事实；私有计数器写 state，不用 note 公开。不要通过 attribute 改写 HP/SAN；用宿主的 damage/sanity 以保留重伤、疯狂等规则。
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
