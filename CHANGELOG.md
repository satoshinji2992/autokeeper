# Changelog

## 0.1.0

- 多智能体回合图（LangGraph）：行动解析 → 裁定 → 引擎结算 → NPC → 守秘人叙事 → 记录；孤注一掷 / 花幸运用 `interrupt` 交给玩家选择。
- BCDice 掷骰与 CoC 7 版结算：理智与疯狂、伤害与重伤、幸运、孤注一掷、对抗、成长；兼容骰娘指令。
- 模组格式、模组图谱校验、编译为 Agent Skill 包；包内附字段齐全的格式样例。
- 模组导入：Opus Agent 读 PDF 文本与页面图片，写 `module.yaml`、画彩色 ASCII 图，自校验后编译。
- Textual 终端界面与浏览器模式：彩色场景与头像、打字机叙事、转动的骰子、理智下降时的画面故障、手稿、手绘、地图。
- 嵌入模式：MCP 服务 + Claude Code 插件（Skills、命令、裁定员子 Agent）+ Codex Skills。
- AI 调查员：陪玩与自动跑团（`Send` 并行出主意）；评测：修辞诱导对照、上下文账本。
