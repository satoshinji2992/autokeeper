```text
 █████╗ ██╗   ██╗████████╗ ██████╗ ██╗  ██╗███████╗███████╗██████╗ ███████╗██████╗ 
██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██║ ██╔╝██╔════╝██╔════╝██╔══██╗██╔════╝██╔══██╗
███████║██║   ██║   ██║   ██║   ██║█████╔╝ █████╗  █████╗  ██████╔╝█████╗  ██████╔╝
██╔══██║██║   ██║   ██║   ██║   ██║██╔═██╗ ██╔══╝  ██╔══╝  ██╔═══╝ ██╔══╝  ██╔══██╗
██║  ██║╚██████╔╝   ██║   ╚██████╔╝██║  ██╗███████╗███████╗██║     ███████╗██║  ██║
╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝     ╚══════╝╚═╝  ╚═╝
                       自 动 守 秘 人 · 克苏鲁的呼唤 7 版中文跑团
```

AutoKeeper 是一个跑在终端里的 AI 守秘人。导入一本 CoC 模组就能开团：

- **守秘人和 NPC**：守秘人讲故事，NPC 各有各的记忆和秘密；
- **规则结算**：掷骰与结算交给 BCDice 和规则引擎，模型不能编骰值；
- **画面**：场景、人物和手稿都是彩色 ASCII 图，理智下降时画面会坏掉；
- **座位**：可以坐真人，也可以坐 AI 调查员；
- **两种玩法**：用独立的终端界面玩，或者把它装进 Claude Code、Codex，让它们来当守秘人。

<p align="center"><img src="docs/img/title.png" width="80%" alt="AutoKeeper 标题画面"></p>

## 它和“让大模型当 KP”有什么不同

| 常见问题 | AutoKeeper 的做法 |
|---|---|
| 模型不熟悉具体模组，整本塞进上下文又贵又漏 | 导入时把模组编译成 [Agent Skill](https://agentskills.io/specification) 包：守秘人平时只带总览，走进哪个地点才读哪个地点的文件 |
| 玩家几句话就能把裁判说服（[CoC-Seduce](https://arxiv.org/abs/2607.02802) 里的“修辞注入”） | 叙事和裁定拆成两个 Agent：行动先被改写成中立描述，裁定员看不到玩家原话；骰子由 BCDice 掷 |
| NPC 说漏嘴、前后矛盾 | 每个 NPC 一个独立会话，只看得到自己的资料和对话；回复前做泄密检查，不该说的会被打回重说 |
| 模型自己“记账”，HP、理智、时间越跑越乱 | 线索、时间、事件、理智、伤害、结局全部由引擎按模组数据结算，存档就是一个 LangGraph 检查点 |

## 快速开始

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 18+。默认模型是守秘人和裁定员用 Opus 5.5、NPC 用 Haiku 5.5，通过 Claude Agent SDK 调用：

- **自己在本机玩**：沿用本机 Claude Code 的登录；
- **部署给别人用**：请设置 `ANTHROPIC_API_KEY`，因为 Anthropic 不允许第三方产品提供 claude.ai 登录。

也可以在 `autokeeper.toml` 里把各个角色换成别家模型。

```bash
uv sync --extra claude
uv run autokeeper setup     # 安装 BCDice、编译模组 Skill 包、链接 .claude/skills 与 .agents/skills
uv run autokeeper           # 打开游戏
uv run autokeeper serve     # 或者在浏览器里玩：http://localhost:8000
```

仓库本身不带模组。CoC 官方模组受版权保护，请先导入你自己手上的（见下一节），导入后会出现在开局菜单里。

游戏里直接说你要做什么，比如“我翻看书桌的抽屉”“我问守墓人最近见过谁”。常用指令：

| 指令 | 作用 |
|---|---|
| `.ra 侦查` `.ra 困难聆听` `.rab 侦查` `.rap2 侦查` | 技能检定、指定难度、奖励骰、惩罚骰 |
| `.sc 1/1d6` `.st 侦查60 hp-2` `.r 1d6+1` `.coc` `.en` | 理智检定、改人物卡、掷骰、掷属性、成长检定 |
| F2 · F4 · F5 · Ctrl+R · Ctrl+T | 线索本与手稿 · 手绘 · 地图 · 上回书说到 · 换配色 |

## 导入你自己的模组

```bash
uv run autokeeper import modules/_import/追书人.pdf --id zhuishuren --pages 8-19
```

导入是一个 LangGraph 流程：

1. **提取**：取出 PDF 每页的文字，并把页面渲染成图片；
2. **构建**：Opus Agent 带着读写文件和运行校验的权限，在 `modules/local/<id>/` 里读原文、看地图页，写出 `module.yaml`，并给每个地点和人物画彩色 ASCII 图；
3. **校验**：检查引用是否完整、地点是否走得到、每个结论有几条线索、图的尺寸和配色蒙版；有问题交还给 Agent 修改，最多三轮；
4. **编译**：生成 Skill 包。

官方模组受版权保护，导入结果只留在本地（`modules/local/`、`skills/module-<id>/` 都在 `.gitignore` 里）。

## 生成原创模组

```bash
uv run autokeeper generate "1925 年上海，一家照相馆的底片里多了一个人" --id zhaoxiangguan
```

- **考据**：考据员（Claude，带 WebSearch / WebFetch）先查这个年代和地点的真实细节，写成带出处的考据卡；
- **编剧**：编剧按三线索原则原创模组并画图；
- **校验与编译**：走和导入相同的校验、修复、编译流程。

## 嵌入 Claude Code / Codex

把游戏引擎当成 MCP 服务，让 Claude Code 或 Codex 自己当守秘人：

- **Claude Code**：仓库本身就是一个插件，包含 Skills、`/autokeeper:play` 命令、`coc-arbiter` 子 Agent 和 MCP 服务：

  ```text
  /plugin marketplace add <本仓库路径或 GitHub 地址>
  /plugin install autokeeper@autokeeper
  /autokeeper:play zhuishuren
  ```

- **Codex**：`autokeeper setup` 已把 `skills/` 链接到 `.agents/skills`；再在 `~/.codex/config.toml` 里注册 MCP：

  ```toml
  [mcp_servers.autokeeper]
  command = "uv"
  args = ["run", "--directory", "/path/to/autokeeper", "autokeeper", "mcp"]
  ```

两种模式用的是同一套 MCP 工具（`skill_check`、`sanity_check`、`npc_say`、`advance_time`……）和同一个 `skills/` 目录。

## 架构

```text
界面    Textual 终端界面 ── textual-serve 浏览器 ── Claude Code / Codex（经 MCP）
          │ 场景图 · 叙事 · 人物卡 · 骰子 · 线索本 · 手绘画布
编排    LangGraph 回合图：parse → rule → resolve ─(interrupt: 孤注一掷/幸运)→ talk → narrate → remember
          │ 检查点 = 存档；Send = AI 调查员并行出主意
Agent   intent(Haiku) · arbiter(Opus) · keeper(Opus) · npc×N(Haiku) · chronicler · investigator · vision · artist · importer
          │ 每个 Agent 一个常驻会话（Claude Agent SDK），后端可换 LangChain / Codex
引擎    BCDice 掷骰 · CoC 7 版结算 · 线索/时间/事件/结局 · 泄密检查
知识    module.yaml → 模组图谱（NetworkX）→ Skill 包（SKILL.md + references/ + assets/ascii/）
```

| 角色 | 默认模型 | 看得到什么 |
|---|---|---|
| 行动解析 intent | Haiku 5.5（low） | 玩家原话、当前场景 |
| 裁定员 arbiter | Opus 5.5（medium） | 中立的行动描述、本场景线索的检定要求、人物卡；看不到玩家原话 |
| 守秘人 keeper | Opus 5.5（medium） | 模组总览；进入地点时才读该地点文件；每轮的结算结果 |
| NPC | Haiku 5.5（medium） | 只有自己的人设、秘密、谎言、能给的线索和自己的对话 |
| 记录员 chronicler | Opus 5.5 | 游戏记录；用评书腔写“上回书说到” |
| AI 调查员 | Haiku 5.5 | 玩家可见的内容和自己的人物卡 |
| 模组构建 importer / 美术 artist | Opus 5.5 | 原文、页面图片；产出模组和彩色 ASCII 图 |

角色到模型的映射在 `autokeeper.toml` 里改，后端可选 `claude`、`langchain`（Gemini、OpenAI 等）、`codex`（接口预留）。

## 实测

| 项目 | 结果 |
|---|---|
| 模组导入：入门套装《追书人》PDF 的 12 页 | Opus Agent 约 20 分钟写出 10 个地点、6 个 NPC、18 条线索、11 个事件、6 种结局和 18 张彩色 ASCII 图，校验 0 错误；补充的内容和取舍由它逐条列出 |
| 渐进式披露（tiktoken o200k 计） | 《追书人》守秘人常驻上下文：整本 14,067 → 平均 3,871 tokens（−72.5%） |
| 自动跑团 | AI 调查员跑《追书人》16 轮无报错，平均每轮约 17 秒（含 AI 玩家思考） |
| 测试 | 22 个（BCDice 实掷、CoC 结算、模组校验与编译、回合图与中断、手绘判定、MCP 工具） |

## 开发

```bash
uv run pytest                                # 规则、模组、引擎与回合图测试（不调用模型）
uv run autokeeper check src/autokeeper/module/example   # 模组与 ASCII 图校验（格式样例）
uv run autokeeper autoplay modules/local/<id> --party 2   # 让 AI 调查员自动跑一局
```

## 版权

- **代码**：MIT。
- **模组内容**：仓库不包含任何模组。导入的模组只留在本地；`src/autokeeper/module/example/` 是字段齐全的格式样例（CC0），只用于测试。
- **规则**：Skill 里的规则摘要是自写的，不含规则书原文。
- **骰子引擎**：[BCDice](https://github.com/bcdice/BCDice)，BSD-3-Clause。
- **商标**：Call of Cthulhu 是 Chaosium Inc. 的商标，本项目与 Chaosium 无关。
