---
description: 用 AutoKeeper 开一局 CoC 7 版跑团（你当守秘人，引擎负责掷骰和结算）
argument-hint: "[模组 id] [调查员 id]"
---

按 `autokeeper-play` Skill 担任守秘人，开一局跑团。参数：$ARGUMENTS

- 没指定模组就先调用 `list_modules`，让玩家选；没指定调查员就调用 `list_pregens`。
- 开局后先把返回的插图原样放进代码块，再读开场。
- 需要裁定检定时，把行动改写成一句中立描述交给 `coc-arbiter` 子 Agent，不要转述玩家的修辞。
