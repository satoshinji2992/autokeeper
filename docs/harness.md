# Harness

AutoKeeper 里所有 Agent 都跑在 `src/autokeeper/harness/` 上。模型只是通道：给它系统提示和一组消息，它回一段文本或一个 JSON。上下文、思考、结构化输出、工具、联网搜索、预算和追踪，都由 harness 自己管。

这样做有三个结果：

- **换模型不丢东西**。每个 Agent 的对话记录存在存档里，读档后换成别的后端照样接着玩。
- **断网也能跑**。工具循环和搜索都不依赖厂商，换成本地模型、关掉搜索，导入和生成照样能跑。
- **每次调用都看得见**。token、缓存命中、延迟、修复次数、花费都写进追踪日志。

| | 谁在管 |
|---|---|
| 对话历史 | harness：`GameState.transcripts`，按字符预算开窗，旧内容由摘要员折叠 |
| 思考强度 | harness：`Role.effort` 映射到各家自己的参数 |
| 结构化输出 | harness：厂商的 schema 模式或提示词 JSON，再做 JSON Schema 校验和一轮修复 |
| 工具调用 | harness：自己的 LangGraph 循环和沙箱工具，不用任何厂商的 tool calling |
| 联网搜索 | harness：Tavily / DuckDuckGo / Wikipedia，可以设成 `none` 离线 |
| 花费 | harness：按 token 估价，超预算就拒绝调用 |

## 模型通道 `models.py`

| 后端 | 用法 | 结构化输出 | 思考 |
|---|---|---|---|
| `claude` | Claude Code（Claude Agent SDK）只当传输层：不给工具，`max_turns=1`，每次回复后在后台 `/clear` | `output_format` JSON Schema | `effort` |
| `langchain` · `deepseek:` | `ChatDeepSeek`，`base_url` 可改 | 提示词 JSON + `response_format: json_object` | `thinking.type` + `reasoning_effort`（low / high / max） |
| `langchain` · `ollama:` | `ChatOllama`，默认 `num_ctx=16384` | Ollama 的 `format`（受约束解码） | `reasoning`，只在 high / max 时开 |
| `langchain` · `openai:` / `anthropic:` / `google_genai:` | `init_chat_model` | 各家 schema 模式；带 `base_url` 的 OpenAI 兼容服务用提示词 JSON | `reasoning_effort` / `thinking.budget_tokens` / `thinking_level` |
| `codex` | 接口预留 | | |

为什么要 `/clear`：同一个 Claude Code 进程里换 `session_id` 隔离不了上下文，实测会串到上一轮的内容；`/clear` 能清掉历史、保留系统提示，大约 0.4 秒。现在它在玩家读叙事的时候在后台跑，不占回合时间。

为了让缓存一直命中，发给 Claude 的请求一条消息一个内容块，只往后追加。这一次的请求正好是下一次的前缀，Claude 和 DeepSeek 的前缀缓存都能命中（实测数据见文末）。

### 结构化输出

`Model.complete(schema=...)` 对所有后端的约定一样：

1. 有可靠 schema 模式的用 schema 模式（Claude、Ollama、OpenAI、Gemini）。没有的（DeepSeek、一般的 OpenAI 兼容服务）就把 schema 和一个自动生成的示例写进系统提示，并打开 JSON 模式。
2. 用 `jsonschema` 校验结果。
3. 不合格就把模型的原输出和校验器的报错一起发回去，修一次。
4. 还不行，调用方退回到安全的默认值，比如裁定员默认“不用掷骰”。

实测：本地 gemma4-31b 当裁定员时，第一次输出没通过 schema 校验，修了一轮就过了。

### 临时故障

每次调用都有超时（`AUTOKEEPER_TIMEOUT`，默认 600 秒）。

- **重试**：限流（429）、过载（529）、5xx、超时、断线都算临时故障，用 tenacity 指数退避加抖动，最多试三次。
- **不重试**：
  - 预算超限、认证失败、请求本身有错（400）；
  - 已经流给玩家的叙事，再重试玩家会看到同一段话出现两遍。
- **Claude Code 进程**：中途出错就丢掉这个进程，下一次重试换新的。
- **追踪**：重试次数写进追踪日志。

## 上下文 `context.py`

`Session` 是一个 Agent 的对话。对话记录是调用方持有的普通列表，游戏里就是 `GameState.transcripts[key]`，跟存档一起保存。每次调用把窗口完整发一遍：

| Agent | 窗口（字符） | 说明 |
|---|---|---|
| keeper | 16,000 | 叙事要前后连贯 |
| npc:\<id\> | 8,000 | 每个 NPC 一份，只有自己的对话 |
| investigator:\<id\> | 6,000 | AI 调查员记得自己刚做过什么 |
| arbiter | 4,000 | 看到最近几次裁定，不会重复给线索 |
| intent / chronicler / vision | 0 | 无状态，需要的东西都在提示里 |

记录超出窗口时，最早的几轮交给摘要员（`summarizer` 角色，用便宜的模型）并进 `GameState.notes[key]`，之后作为窗口的第一条消息出现。`Role.window` 可以按模型改窗口，比如本地小模型改小一些。

折叠在后台进行，回复先交给玩家，下一次调用前才等折叠完成。实测摘要员一次要 6 秒左右，这样玩家不用干等。

- **顺序**：先拿到新摘要，再一起删旧记录、写摘要，中途存档不会丢内容。
- **兜底**：摘要员失败时，退回到保留最近一段原文。

`send(prompt, extra=...)`：`extra` 只这次发，不进记录。NPC 泄密检查用的就是它。

1. 泄密检查发现回复里有不该说的内容；
2. `forget()` 删掉这一轮；
3. 带着一句不入记录的提醒重答。

这样，泄露的话从来不会变成 NPC“说过的话”。

## Agent 循环 `loop.py`

导入模组、生成模组、考据核实都用同一个循环：

```text
START → think ─┬─ finish ─────────→ END
          ↑    └→ act ─┬─ 步数上限 → END
          └────────────┘
```

模型每一步只输出一个扁平的 JSON：`thought`、`tool`，再加上这个工具要用的字段（`path`、`content`、`old`、`new`、`query`、`url`、`start`、`limit`）。任何后端都能产出这个格式，所以不需要厂商的 tool calling。

- 工具的错误（路径越界、参数缺失、原文不唯一、搜索失败）作为观察结果回给模型，由模型自己改，任务不会因此中断。
- 观察结果最多 12,000 字；最后 5 步会提醒模型收尾。
- 会话带一个工作摘要员：旧步骤被折叠成进度摘要（目标、已完成、关键事实及出处、写过的文件、还没做的事）。
- 导入任务还要求模型把要点记进 `notes.md`，作为长期记忆。

### 工具 `tools.py`

| 工具 | 作用 |
|---|---|
| `ls` `read_file` `grep` | 读工作区（带行号分段读，正则搜索） |
| `write_file` `append_file` `edit_file` | 写工作区；`edit_file` 要求原文唯一 |
| `view_image` | 读图员（vision 角色）看图回答，所以 Agent 自己的模型可以只认文字 |
| `check` | 在进程里跑模组校验器 |
| `web_search` `fetch_url` | 联网搜索，网页用 MarkItDown 转成 Markdown 分段读 |

工作区（`Workspace`）把路径解析到根目录之下，越界直接拒绝。写入只允许落在白名单里，导入时是 `module.yaml`、`notes.md`、`art/*`。

### 搜索 `search.py`

`auto`：有 `TAVILY_API_KEY` 用 Tavily，否则用 DuckDuckGo（`ddgs`），再不行用 Wikipedia。`none`：工具直接告诉模型“联网已关闭”，考据卡全部标“未核实”，之后联网了可以用 `autokeeper research` 补核。

## 预算与追踪

- `spend.py`：按 token 估价（DeepSeek 按峰时价，所以估高不估低）。配置了 `budget_usd`，超出后按量计费的调用全部拒绝。账本在 `saves/spend.json`。
- `trace.py`：每次调用一行 JSON，写进 `saves/traces/<日期>.jsonl`。记录 Agent、后端、模型、思考强度、输出方式（text / native / json）、输入字数、token、缓存命中、思考字数、耗时、修复次数、错误。`AUTOKEEPER_TRACE=full` 还会记下完整的提示、输出和思考内容。

## 换模型

```bash
uv run autokeeper doctor --ping                        # 每个角色走哪个模型，密钥齐不齐，实际发一句测通
uv run autokeeper --preset deepseek                    # DeepSeek：flash，思考强度按分工
AUTOKEEPER_MODEL=ollama:qwen3:14b uv run autokeeper --search none   # 全部本地、完全离线
```

```toml
# autokeeper.toml 或 ~/.config/autokeeper/config.toml
preset = "deepseek"
budget_usd = 5
search = "auto"

[roles.keeper]               # 单个角色单独改
backend = "langchain"
model = "deepseek:deepseek-v4-pro"
effort = "high"

[roles.all]                  # 对所有角色生效，比如自建的 OpenAI 兼容服务
base_url = "http://localhost:8000/v1"
```

密钥从环境变量读（`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`GOOGLE_API_KEY`、`ANTHROPIC_API_KEY`），也会读 `~/.config/autokeeper/.env` 和仓库根目录的 `.env`（已被 gitignore）。

## 实测

同一个模组（《追书人》）、同一个 AI 调查员，用 `autokeeper autoplay` 跑，数字取自 `saves/traces/`：

| 后端 | 回合 | 调用 | 输入 token | 缓存命中 | 修复 | 耗时 | 花费 |
|---|---|---|---|---|---|---|---|
| DeepSeek flash（思考按分工） | 4 | 17 | 54,890 | 35,200（64%） | 0 | 33.3 s | 估算 $0.010 |
| Claude Opus 5.5 + Haiku 5.5 | 2 | 10 | 85,079 | 51,242（60%） | 0 | 76.7 s（含进程启动） | 订阅 |
| 本地 gemma4-31b Q4（Ollama，离线，不开思考） | 2 | 10 | 33,672 | Ollama 不报 | 2 | 57.0 s | 0 |

- 两回合都含开场叙事。Claude 那局是冷启动，每个 Agent 的第一次调用都要拉起一个 Claude Code 进程；在 TUI 里，这些进程在开场动画期间就预热好了。
- Claude 的 keeper 每次调用命中的缓存依次是 2,643 → 8,247 → 13,254 token，窗口只往后追加的效果就体现在这里。
- 离线那局（`--search none`）用 `sys.addaudithook` 记录了 Python 进程的每次 `socket.connect`：一回合里只有 5 次，全部连向 `127.0.0.1:11434`（Ollama），没有 DNS 查询。向量检索用的是本地缓存的 fastembed 模型，BCDice 走 stdin/stdout。
- DeepSeek 驱动的模组生成，前 115 步共发出 335 万输入 token，其中 98% 命中前缀缓存，修复 2 次。
