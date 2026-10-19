# 模组格式

一个模组是一个目录：

```text
modules/<id>/
  module.yaml        剧情、地点、NPC、线索、事件、结局、预设调查员
  art/<名字>.txt     ASCII 图（只用可打印 ASCII；场景 ≤56×14，头像 ≤18×7）
  art/<名字>.color   同形状的配色蒙版，每个字符一个颜色键，“.”为默认色
  art/<名字>.png     原作图片（也支持 jpg、jpeg、webp），与 ASCII 图可共用图片键
  art/<名字>.source.json  原图来源记录，编译时一并保留
  art/<名字>.credit.json  照片或生成图的来源与授权（Codex 生成的写“AI 生成（Codex image_gen）”和所依据的原图）
  plugins/<id>/manifest.json  插件名称、版本、能力、动作与可执行测试
  plugins/<id>/plugin.py      自定义规则代码，独立进程执行
```

完整字段见 `src/autokeeper/module/schema.py`；字段齐全的格式样例见 `src/autokeeper/module/example/`（只用于测试和给导入 Agent 参考，不是可玩的模组）。

## 主要字段

| 字段 | 说明 |
|---|---|
| `hook` / `opening` | 玩家已知的来意 / 守秘人开场素材；开场素材融入一次叙述，不重复朗读 |
| `truth` / `keeper_notes` | 只给守秘人看的真相和跑团建议 |
| `cover` | 无剧透的原作封面图片键；选模组时显示 ASCII 预览，不作为开局场景 |
| `plugins` | 已开发并通过测试的插件 id 列表；从 `plugins/<id>/` 加载代码，不能在 YAML 中填运行时 `extensions` |
| `start` / `start_time` | 起始地点 id 和时间（如 `D1 14:00`） |
| `locations` | 地点：`read_aloud`（读给玩家）、`keeper`（幕后资料）、`features`、`exits`、`art`；`public: true` 表示可问路找到的公共地标；`travel_minutes` 指定相邻路段分钟数；`within` 填外层地点 id，表示确实在它里面（书房在宅子里、陵墓在公墓里），地图上画在外层地点里，可留空；`looks` 是剧情变样后的场景图 |
| `npcs` | NPC：人设、`knows` / `secrets` / `lies`、能给的 `clues` 和对应的社交门槛 `gates`、`portrait`、变样头像 `looks` |
| `items` | 物件：`id`、`name`、公开外观 `description`、所在地点 `where`、初始持有者 `owner`（可空）、配图 `art`；后续归属随本局保存，不替代线索或授予隐藏效果 |
| `clues` | 线索：在哪（`where` / `feature` / `npc`）、检定（`check`）、支持结论（`points_to`）、关键性（`core`）、理智损失、附带手稿；`requires` 列出必须全部满足的前置线索 id 或 `flag:标记`，引擎会阻止提前发出 |
| `handouts` | 手稿完整 `text`；`images: [病历图片键, 地图图片键]` 关联原图，仅发现后显示，支持滚动阅读全文、ASCII 预览和查看原图 |
| `conclusions` | 玩家需要推出的结论，校验时要求每个结论至少两条线索 |
| `flags` | 剧情标记及其含义；裁定员只会在行动真正达成含义时设置 |
| `events` | 定时（`D1 21:00`）、到达（`visit:地点`）或标记（`flag:x`）触发；可以移动队伍、要求理智检定、让 NPC 登台表演（`performer` + `performance`）、直接进入结局 |
| `endings` | `condition: flag:x` 的结局按顺序判定；奖励里写 `理智 +1D6` 会自动结算 |
| `sketches` | 让玩家手绘的东西：`target` 写真实样子，读图员相似度过 `threshold` 时触发理智检定或线索 |
| `showcases` | 大场景：在重要节点整屏展示一张大图，每个模组 5–15 个，见下文 |
| `pregens` | 预设调查员（属性用中文名：力量、体质、体型、敏捷、外貌、智力、意志、教育） |

## 剧情变样（looks）

原文写明某个地点或人物会明显变样（被烧毁、被淹、受伤、死去），而且有标记表示这件事发生了，就在那个地点或 NPC 下写：

```yaml
looks:
  - when: flag:church_burned        # 标记成立后换图
    change: 礼拜堂烧成焦黑的空壳，屋顶塌了一半
    art: church_burned              # 可省，默认 <原图键>_<标记>
```

导入时若本机有 Codex，就照原图画出 `art/church_burned.jpg`；没画成的，游戏里标记成立时再画。昼夜不用写，画面按时间自动调光。每个地点或人物一般最多两种，全模组最多画 6 处。

## 大场景（showcases）

开局的氛围、突发事件、生死关头、关键地点、怪物现身、最终 boss 和每个结局，各可以写一个大场景。到了那一刻，屏幕上只剩一张大图和一行短标题；叙述在后面照常进行，按任意键、点一下或约 8 秒后自动收起，要掷骰时让路。每局每个最多出现一次。

```yaml
showcases:
  - id: sc_horde
    kind: 最终boss                  # 开场 / 突发 / 危机 / 重要场景 / 怪物 / 最终boss / 结局
    when: event:e_horde             # opening / visit:地点 / event:事件 / creature:怪物 / flag:标记 / ending:结局
    title: 细碎的脚步声              # 玩家此刻看得见的短标题，不剧透
    prompt: 月光下的墓地，几十张犬一样的脸从墓碑后面探出来……   # 画面描述，只在房主这边
    base: mudi                      # 可省：照这张原图重画；默认用触发的地点的图
    art: ""                         # 可省：模组自带的、正好合适的图
```

图按这个顺序来：模组自带的 `art`；本机有 Codex 时照参考图（`base`、触发的地点，或联网搜到的照片）重画，没有参考就照描述画；没有 Codex 就不生成，只把模组里那个地点的图或搜到的照片原样放大（联网搜索关掉时不搜）。生成的图放在 `saves/showcases/<模组>/`，不进模组目录，计入每个模组 100 张的额度。游戏开始后在后台一张一张提前准备，开场优先；到点还没准备好就跳过，不等。`autokeeper showcase <模组>` 可以在开局前全部准备好。`creature:` 在怪物的名字第一次出现在事件或叙述里时触发，要精确控制就用 `event:` 或 `flag:`。导演即兴时也可以加，每局最多三个，图跟着存档。

## 开局与日常

`start` 是玩家第一次能行动的位置。可以从车站、街口或旅店引入，再由玩家决定怎么赴约；不要在 `opening` 里提前替玩家见完委托人、安顿完行李、选好调查方向。开局地点必须在 `locations` 中，并用 `exits` 接入地图。

《追书人》从火车站开始，经过镇中心与艾尔斯伯里大街到金博尔家。玩家可以沿途问路、吃早饭、闲聊，也可以直接赶路；直接前往远处时，守秘人会收到沿途地点的公开描述。路过不等于进门调查或取得线索。

生活场景可以没有线索、检定或恐怖事件。NPC 要有自己的营生和牵挂，按玩家兴趣展开；已有的剧情时钟仍会走。导入时补的抵达场景和普通人物，在 `keeper_notes` 中注明“AutoKeeper 补充”，不改原作案情与必需事件。设计依据见 [守秘人体验设计](keeper-experience.md)。

路段默认 10 分钟；例如 `travel_minutes: {town: 15}` 把去相邻 `town` 的时间设为 15 分钟，反向未写时沿用这一数值。双向都写时必须一致。路线选择按累计耗时计算；普通一句问答可以不推进时钟，吃饭、搜索和休息才按实际耗时推进。不要把秘密入口标成 `public`。

## 原作图片与信息完整性

导入 PDF 时保留逐页原文、页面预览和去重后的内嵌原图（`source/images/index.json`）。导入 Agent 用 `use_image` 原样复制图片，用 `write_art` 批量保存 ASCII 图与配色。场景原图的键要与 `locations.art` 对应；完整病历、地图、信件另用 `handouts.images` 绑定，不能只写进被运行时覆盖的 `pictures` 字段。

默认用彩色 ASCII 显示，原图仍保存；文字转录必须可独立阅读，字迹无法辨认时注明不确定，不猜写。完整版地图、怪物真容和带幕后文字的整页不能提前展示。联机只传已公开的场景图和已发现的手稿图；手稿传送较大尺寸供放大阅读，房主仍保留完整原文件。原作特有但引擎未实现的规则须在 `keeper_notes` 明确记录，不能改成无条件伤害或虚构固定时间线。

## 校验与编译

编译 Agent 可以用 `write_plugin` 实现疾病、计数器和自定义行动；即兴导演也可以提交带代码与测试的 `plugins`，通过校验后加入当前存档。插件在开局、到达、回合结束和已声明的自定义动作时执行。详见 [插件开发](plugins.md)。

```bash
uv run autokeeper check modules/<id>      # 引用、可达性、线索冗余、ASCII 尺寸与蒙版
uv run autokeeper art color modules/<id>  # 让美术 Agent 给还没上色的图画蒙版
uv run autokeeper compile modules/<id>    # 生成 skills/module-<id>/（Agent Skill 包）
```

编译出的 Skill 包遵循 [Agent Skills 规范](https://agentskills.io/specification)：`SKILL.md` 是守秘人随身带的总览，
`references/locations/*.md`、`references/npcs/*.md`、`references/handouts/*.md` 等到需要时才读，`assets/ascii/` 放字符画，`assets/images/` 保留原图和来源。导入模组还会把完整原文、逐页扫描和图片档案复制到 `assets/source/`，只供守秘人回查；整理稿不代替完整原始资料。
