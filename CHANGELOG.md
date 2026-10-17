# Changelog

## 0.3.0

- harness：
  - **备用模型与熔断**：`Role.fallback`、`--fallback`、`AUTOKEEPER_FALLBACK`。主模型重试用完仍失败或超预算时改用备用模型；失败的模型冷却 5 分钟，期间所有 Agent 一起绕开它；叙事已经流出时不降级。
  - **`autokeeper trace`**：按 Agent / 模型 / 后端 / 运行汇总追踪，包括调用、token、缓存命中、延迟 p50/p95、修复、重试、降级、错误、花费。每行追踪记运行编号，autoplay 用存档名。
  - **`Session.send(remember=...)`**：记录里只存精简版提示。裁定员和 AI 调查员用它，摘要折叠少了六成多。
  - DeepSeek 预设关掉摘要员的思考（实测缓存按思考强度分区，开着思考换不来缓存）。
  - 模型串路由：不带前缀的 `claude-*` 走 Claude Code，其余交给 `init_chat_model`。
- 回合图：去别处做事（“我走到陵墓石门前看门缝”）也会填目的地，裁定员按目的地的场景裁定，引擎先移动再结算。以前队伍会卡在原地，相邻地点的线索永远拿不到。
- 用 DeepSeek 完整跑通《追书人》：53 回合打到结局“最后一晚”，估算 $0.15；每回合花费比修之前低 43%。
- 天气：地点可以写 `weather`（雨、雷雨、雪、雾、落叶、浮尘、炉火、灯影摇曳、手电），不写就按地点名、描述和时间推断，叠在照片和手绘 ASCII 上实时动画；F6 开关。

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
