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

结果待在《追书人》上重测后补上（`saves/eval/rhetoric.json` 里有每条话术、两次裁定和理由）。
