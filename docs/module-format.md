# 模组格式

一个模组是一个目录：

```text
modules/<id>/
  module.yaml        剧情、地点、NPC、线索、事件、结局、预设调查员
  art/<名字>.txt     ASCII 图（只用可打印 ASCII；场景 ≤56×14，头像 ≤18×7）
  art/<名字>.color   同形状的配色蒙版，每个字符一个颜色键，“.”为默认色
```

完整字段见 `src/autokeeper/module/schema.py`；字段齐全的格式样例见 `src/autokeeper/module/example/`（只用于测试和给导入 Agent 参考，不是可玩的模组）。

## 主要字段

| 字段 | 说明 |
|---|---|
| `hook` / `opening` | 玩家可见的钩子和开场朗读 |
| `truth` / `keeper_notes` | 只给守秘人看的真相和跑团建议 |
| `start` / `start_time` | 起始地点 id 和时间（如 `D1 14:00`） |
| `locations` | 地点：`read_aloud`（读给玩家）、`keeper`（守秘人须知）、`features`（可调查物）、`exits`、`art` |
| `npcs` | NPC：人设、`knows` / `secrets` / `lies`、能给的 `clues` 和对应的社交门槛 `gates`、`portrait` |
| `clues` | 线索：在哪（`where` / `feature` / `npc`）、要什么检定（`check`）、支持哪个结论（`points_to`）、是否关键（`core`）、发现时的理智检定、附带手稿 |
| `conclusions` | 玩家需要推出的结论，校验时要求每个结论至少两条线索 |
| `flags` | 剧情标记及其含义；裁定员只会在行动真正达成含义时设置 |
| `events` | 定时（`D1 21:00`）、到达（`visit:地点`）或标记（`flag:x`）触发；可以移动队伍、要求理智检定、让 NPC 登台表演（`performer` + `performance`）、直接进入结局 |
| `endings` | `condition: flag:x` 的结局按顺序判定；奖励里写 `理智 +1D6` 会自动结算 |
| `sketches` | 让玩家手绘的东西：`target` 写真实样子，读图员相似度过 `threshold` 时触发理智检定或线索 |
| `pregens` | 预设调查员（属性用中文名：力量、体质、体型、敏捷、外貌、智力、意志、教育） |

## 校验与编译

```bash
uv run autokeeper check modules/<id>      # 引用、可达性、线索冗余、ASCII 尺寸与蒙版
uv run autokeeper art color modules/<id>  # 让美术 Agent 给还没上色的图画蒙版
uv run autokeeper compile modules/<id>    # 生成 skills/module-<id>/（Agent Skill 包）
```

编译出的 Skill 包遵循 [Agent Skills 规范](https://agentskills.io/specification)：`SKILL.md` 是守秘人随身带的总览，
`references/locations/*.md`、`references/npcs/*.md`、`references/handouts/*.md` 等到需要时才读，`assets/ascii/` 放插图。
