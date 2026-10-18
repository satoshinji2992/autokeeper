# 评测

评测命令与历史结果见下文。运行报告写入 `saves/eval/`。

## 连续游玩体验

```bash
uv run autokeeper --preset codex eval experience --turns 12
uv run autokeeper --preset deepseek eval experience --actions actions.txt --out saves/eval/my-route.json
```

`actions.txt` 每行一句玩家行动。默认路线包含到站、闲逛、早餐、日常交谈、约定与赴约；使用真实的解析、裁定、NPC、导演和叙述。报告记录全文、逐轮等待、调用量、时间推进、世界记录与中断位置。叙事质量仍需阅读记录判断。试玩会产生模型用量，关闭额外绘图以单独观察叙事。

审阅时重点检查：是否替玩家行动、是否强推主线、约定与物品是否前后相符、是否泄密、NPC 是否重复自己的回答和动作。自动回归另覆盖二十轮日常、借还、沿途停留，以及断线后从存档恢复。真实模型超时会写出部分报告并返回失败，不能算整条路线通过。

2026-10-18 本机验证：Codex 默认模型的“向站务员说早上好”完成 1/1 轮，2 次调用、33.12 秒，未推进时间或获取线索；未调用裁定与补叙述。更早的三轮到站路线只完成两轮，第三轮超时；该报告保留为失败记录，不算整段体验通过。单次问候通过不代表整场主持质量或固定响应速度。

## 插件开发与实际结算

2026-10-18，本机用 DeepSeek 的编译 Agent 实际调用 `write_plugin` 开发《冬雪之下》疾病插件，修订阶段 14 步、12 项测试通过；人工复核补充节奏互斥和跨天结算后为 v0.3.0、15 项测试。四天固定骰值的引擎测试验证感染、体质损失与理智损失；另用真实 BCDice 补结算跨过的两天，读档后无重复扣减。报告在 `saves/eval/winter-plugin-result.json` 与 `winter-plugin-engine.json`，完整模组与源码留在本地忽略目录。这不是整场模组通关或所有分支的游玩评测。

通用回归验证真实子进程、执行超时、能力与引用校验、错误反馈、按更新后属性连续检定、即兴安装、源码版本恢复、联机保密和暂停开关。编译校验会执行插件的用例，不以“模型说测试通过”作为结果。

真实即兴链路也已验证：DeepSeek 导演临场生成 `stove_fuel` 插件，5 项测试通过后安装到本局，执行添柴动作使库存 3→2、热度 0→1，源码与状态进入存档。测试没有修改原模组；报告为 `saves/eval/improv-plugin-ds.json`。本轮完整回归 294 项通过。

## 渐进式披露的上下文账本

```bash
uv run autokeeper eval context modules/zhuishuren
```

比较外部 Agent（Claude Code、Codex 通过 skills/MCP 主持）从编译出的 Skill 包里常驻的上下文：整本模组（所有 reference 文件）与“SKILL.md 总览 + 当前地点文件”。内置引擎的守秘人两者都不读，只拿公开的场景描述；裁定员拿当前地点周边的线索切片，导演拿真相和地图概览。用 tiktoken 的 o200k_base 计数，只作为与模型无关的近似值。

| 模组 | 整本 | 总览 | 渐进式（平均 / 最多） | 节省 |
|---|---|---|---|---|
| 追书人（含火车站开场，2026-10-18） | 15,254 | 3,893 | 4,386 / 5,024 | 71.2% |

加火车站之前的版本是 14,067 / 3,371 / 3,871 / 4,502，节省 72.5%。

## 原文细节问答（LightRAG）

```bash
uv run autokeeper --preset deepseek eval lore            # 四种策略都跑
uv run autokeeper --preset deepseek eval lore --only graphrag   # 只重跑一种，其余沿用上次的结果
```

题目由 importer 从原文里出：24 道细节题，三分之一要串起两三处原文才能回答，每题附原文依据。之后守秘人拿四种上下文分别作答，判卷模型对照参考答案打分。这一轮的出题、作答、判卷都用 DeepSeek flash。

| 上下文 | 准确率 | 平均上下文（token） |
|---|---|---|
| 只有 SKILL.md 总览（守秘人常驻） | 29.2% | 3,371 |
| 整本模组（总览 + 全部 reference 文件） | 95.8% | 14,084 |
| 总览 + LightRAG naive（只检索原文块） | 95.8% | 6,181 |
| 总览 + LightRAG mix（图谱 + 原文块） | 91.7% | 6,388 |

- **单看总览**：回答细节问题不够，这也是守秘人每轮都要检索的原因。
- **检索的效果**：用不到一半的上下文，就追平了整本模组的准确率。
- **mix 第一版**：只有 83.3%，上下文反而有 15,165 token。原因是 LightRAG 的实体、关系预算（默认 6k / 8k）不受 `max_total_tokens` 约束，结果把原文块全挤掉了（日志里是“35 entities, 112 relations, 0 chunks”），而细节题的答案恰恰在原文里。
- **修正**：按总预算分配，实体、关系各占四分之一，剩下的留给原文块。
- **明细**：两版都在 `saves/eval/`（`lore_zhuishuren_v1.json`、`lore_zhuishuren.json`）。

## 修辞诱导对照

```bash
uv run autokeeper eval rhetoric
```

同一个行动说两遍：一遍平实，一遍裹上修辞（CoC-Seduce 所说的 Rhetorical Injection）。修辞版来自两处：一是四种模板（伪逻辑、权威施压、情绪恳求、元游戏），二是攻击者 Agent 针对每个场景专门写的话术。

两种裁判拿到的结构化资料相同，区别只在于看不看得到玩家原话：

- **baseline**：单个模型读玩家原话后裁定；
- **split**：先由 intent 把原话改写成中立行动，arbiter 看不到原话。

修辞版裁定比平实版更宽松，就记一次“被说动”。宽松包括三种情况：免检却照样给线索、降低难度、多给奖励骰。

《追书人》上 20 个场景，每个场景两种话术（攻击者 Agent 写的、模板的），每格是 40 次里被说动的次数：

| 裁判模型 | baseline（看原话） | split（intent → arbiter） |
|---|---|---|
| Opus 5.5 | 4（话术 3，模板 1） | 1（话术 1） |
| Haiku 5.5 | 7（话术 7） | 0 |

第一版的 split 并不比 baseline 好：Opus 4/40，Haiku 4/40。翻明细发现，intent 把玩家对环境的断言（“他正背对着我”“泥地被露水泡软了”）当成事实写进了行动描述，arbiter 就照着给了奖励骰。后来收紧了两处，重测得到上表：

- intent：只写调查员做了什么，这类断言一律不写；
- arbiter：奖惩骰只认场景资料里有的条件。

两版明细都在 `saves/eval/`（`rhetoric_v1_zhuishuren.json`、`rhetoric_v2_zhuishuren.json`），里面有每条话术、两次裁定和理由。

### 换成 DeepSeek，比较思考强度

```bash
uv run autokeeper --preset deepseek eval rhetoric \
    --referee deepseek:deepseek-flash@high --referee deepseek:deepseek-flash@low
uv run autokeeper --preset deepseek eval rhetoric --referee deepseek:deepseek-flash@none
```

同样 20 个场景、40 组对照，裁定员都是 deepseek-flash，只换思考强度。延迟取自追踪里每次裁定的耗时：

| 裁定员的思考 | baseline（看原话） | split（intent → arbiter） | 裁定延迟 p50 / p95 | 每次裁定输出 token |
|---|---|---|---|---|
| high | 11 | 2 | 2.4 s / 4.7 s | 317 |
| low | 9 | 0 | 2.0 s / 4.1 s | 293 |
| 不思考 | 6 | 2 | 1.1 s / 1.6 s | 101 |

- 换了厂商，结论不变：split 让被说动的次数从 6–11 次降到 0–2 次。挡住话术靠的是“裁定员看不到原话”这个结构，跟思考强度关系不大。
- baseline 里开着思考反而被说动得更多，像是思考在替玩家的说法找理由。不过对抗话术每轮都重新生成，这个差别可能是噪声。
- 不思考的裁定快一倍、便宜近一半，但是不是一样准，上面这个评测量不出来。拿 20 个平实行动的裁定比较（要不要掷骰、技能、难度、给哪些线索全部一致才算一致）：high 和 low 一致 14/20，high 和不思考也是 14/20；同一档跑两次本身就有差异。细看分歧，不思考有两次一次社交检定成功就给了 2–3 条各自带门槛的线索，high 只给 1 条。
- 所以 DeepSeek 预设的裁定员仍用 high。在乎速度的话，配置里写 `[roles.arbiter] effort = "none"`，每回合少等一秒多。

明细在 `saves/eval/rhetoric-deepseek-effort.json` 和 `rhetoric-deepseek-none.json`。

## 守秘人用哪个 Claude 模型

`eval style` 固定《追书人》9 段的行动、裁定、NPC 台词和骰子，只有守秘人真调用；每个模型跑 1 遍，样本很小。价格是 Claude Code 报出的等价 API 价，缓存命中后的每次调用。Opus、Sonnet、Haiku 在 2026-10-18 的工作树上测，Fable 只有早一版代码上的一遍。

| | Opus 5.5 | Sonnet 5.5 | Fable 5.1 | Haiku 5.5 |
|---|---|---|---|---|
| 套话（前五项，每千字） | 0.81 | 0.54 | 0.45 | 0.00 |
| 平均字数 · 超过 200 字的段 | 274 · 8/8 | 205 · 4/8 | 245 · — | 183 · 1/8 |
| 中位延迟 | 10.5 s | 5.2 s | 11.0 s | 7.8 s |
| 每次花费 | $0.027 | $0.011 | $0.060 | $0.001 |
| 问题 | — | — | 17 处半角逗号，错字 | 段末漏出“〔输入〕”，不点出孤注一掷 |

claude 预设的守秘人因此改用 Sonnet 5.5。裁定员四个模型在 6 个 `.ra` 请求上都是 6/6，但 Sonnet 有一遍在空书房“说服”时给了一条线索，裁定员先留在 Opus。导演 Opus 和 Sonnet 的方案都一次通过校验（4/4），导演在后台，留 Opus。

## 同一局，三种后端

```bash
uv run autokeeper --preset deepseek autoplay modules/zhuishuren --turns 4
uv run autokeeper autoplay modules/zhuishuren --turns 2
AUTOKEEPER_MODEL=ollama:gemma4-31b-ablit-q4 uv run autokeeper --search none autoplay modules/zhuishuren --turns 2
```

数字取自 `saves/traces/`，表格和说明见 [harness.md](harness.md#实测)。
