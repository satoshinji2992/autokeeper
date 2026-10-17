# 评测

所有评测都能复现，结果写在 `saves/eval/`。

## 渐进式披露的上下文账本

```bash
uv run autokeeper eval context modules/local/zhuishuren
```

比较守秘人的常驻上下文：整本模组（Skill 包里所有 reference 文件）与“SKILL.md 总览 + 当前地点文件”。用 tiktoken 的 o200k_base 计数，只作为与模型无关的近似值。

| 模组 | 整本 | 总览 | 渐进式（平均 / 最多） | 节省 |
|---|---|---|---|---|
| 追书人（本地导入） | 14,067 | 3,371 | 3,871 / 4,502 | 72.5% |

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

## 同一局，三种后端

```bash
uv run autokeeper --preset deepseek autoplay modules/local/zhuishuren --turns 4
uv run autokeeper autoplay modules/local/zhuishuren --turns 2
AUTOKEEPER_MODEL=ollama:gemma4-31b-ablit-q4 uv run autokeeper --search none autoplay modules/local/zhuishuren --turns 2
```

数字取自 `saves/traces/`，表格和说明见 [harness.md](harness.md#实测)。
