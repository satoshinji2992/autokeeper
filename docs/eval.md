# 评测

所有评测都能复现，结果写在 `saves/eval/`。

## 渐进式披露的上下文账本

```bash
uv run autokeeper eval context modules/zhuishuren
```

比较守秘人的常驻上下文：整本模组（Skill 包里所有 reference 文件）与“SKILL.md 总览 + 当前地点文件”。用 tiktoken 的 o200k_base 计数，只作为与模型无关的近似值。

| 模组 | 整本 | 总览 | 渐进式（平均 / 最多） | 节省 |
|---|---|---|---|---|
| 追书人（本地导入） | 14,067 | 3,371 | 3,871 / 4,502 | 72.5% |

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

## 同一局，三种后端

```bash
uv run autokeeper --preset deepseek autoplay modules/zhuishuren --turns 4
uv run autokeeper autoplay modules/zhuishuren --turns 2
AUTOKEEPER_MODEL=ollama:gemma4-31b-ablit-q4 uv run autokeeper --search none autoplay modules/zhuishuren --turns 2
```

数字取自 `saves/traces/`，表格和说明见 [harness.md](harness.md#实测)。
