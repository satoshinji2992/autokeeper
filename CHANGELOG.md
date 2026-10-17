# Changelog

## 0.2.0

- 自研 harness（`src/autokeeper/harness/`），所有 Agent 都改跑在它上面：
  - **模型通道**无状态。Claude Code 只当传输层，每次回复后在后台 `/clear`。LangChain 后端加入 DeepSeek、Ollama，以及任意 OpenAI 兼容服务。
  - **结构化输出**：用厂商的 schema 模式或提示词 JSON（DeepSeek 用 JSON 模式），经 JSON Schema 校验，不合格修一轮，最后有兜底。
  - **思考强度**：`effort` 映射到各家自己的参数，思考内容进追踪日志，不再重发给模型。
  - **上下文**：每个 Agent 的对话记录存进存档，按字符预算开窗，旧内容由 summarizer 折叠。窗口只往后追加，让前缀缓存一直命中。
  - **NPC 泄密回滚**：泄露的那一轮从 NPC 记忆里删掉，再带一句不入记录的提醒重答。
  - **Agent 循环**：基于 LangGraph 的 think → act，每步一个扁平 JSON。工作区沙箱化，提供文件、看图、校验、搜索、读网页等工具；工具出错作为观察结果交给模型；有步数上限和进度摘要。
  - **搜索**：Tavily / DuckDuckGo / Wikipedia 可选，`none` 为离线。
  - **花费**：预算护栏，`budget_usd` 加 `saves/spend.json` 账本。
  - **追踪**：每次调用一行，写进 `saves/traces/`。
- 模组导入、原创生成、考据核实都改由 harness Agent 完成，不再依赖 Claude Code 自带的工具，任何模型都能跑，也可以离线。
- 新的全局选项 `--preset`（claude / deepseek）、`--model`、`--search`、`--budget`；`doctor` 显示角色路由、密钥、搜索后端和花费，`--ping` 实测每个模型。
- 实测记录：
  - DeepSeek 和本地 Ollama（离线）都跑通了整局；
  - 用 DeepSeek 生成的原创模组《第三声部》校验一次通过；
  - 修辞注入评测 v2：split 让 Haiku 被说动的次数从 7/40 降到 0/40，Opus 从 4/40 降到 1/40。

## 0.1.0

- 多智能体回合图（LangGraph）：行动解析 → 裁定 → 引擎结算 → NPC → 守秘人叙事 → 记录；孤注一掷 / 花幸运用 `interrupt` 交给玩家选择。
- BCDice 掷骰与 CoC 7 版结算：理智与疯狂、伤害与重伤、幸运、孤注一掷、对抗、成长；兼容骰娘指令。
- 模组格式、模组图谱校验、编译为 Agent Skill 包；包内附字段齐全的格式样例。
- 模组导入：Opus Agent 读 PDF 文本与页面图片，写 `module.yaml`、画彩色 ASCII 图，自校验后编译。
- Textual 终端界面与浏览器模式：彩色场景与头像、打字机叙事、转动的骰子、理智下降时的画面故障、手稿、手绘、地图。
- 嵌入模式：MCP 服务 + Claude Code 插件（Skills、命令、裁定员子 Agent）+ Codex Skills。
- AI 调查员：陪玩与自动跑团（`Send` 并行出主意）；评测：修辞诱导对照、上下文账本。
