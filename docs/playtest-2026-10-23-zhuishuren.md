# 《追书人》独立试玩报告 · 2026-10-23

本局已经走到程序记录的「最后一晚」结局，存档为 `farewell`、`ended=true`。调查能连续推进，但高潮在第一次向来客问好时就被系统收束，真相线索、理智结算和后续交谈均未完整完成。因此，这次验证了“能到结局”，尚不能据此认定主线体验合格。

**试玩边界与结果**

基线为 `9587b2c`，工作区 `/root/code/autokeeper-wt/release`。一名预设调查员哈罗德·布伦南，由试玩代理根据已经展示的内容逐轮扮演；实际 DeepSeek 负责主持、裁定与人物，实际 BCDice 掷骰。未使用调查员模型、预写行动队列或指定骰点。结局前没有读取源模组、隐藏线索、后台裁定或未来剧情。T3 完整结算后按统一驱动适配要求关闭并恢复同一存档一次，未重开、改存档或修改游戏代码。

首次开场请求为北京时间 01:19:53，结局完成为 01:34:00，墙钟约 14 分 7 秒，共 19 个玩家行动回合。游戏内从 D1 08:15 到 D1 22:12。最终 HP 12/12、SAN 55、幸运 50，无战斗、受伤或疯狂。成功与失败均保留，侦查孤注一掷的大失败确实造成损坏，约定从报酬扣 5 美元。

最终落盘统计为 101 次模型调用、输入 1,221,426 token、输出 76,404 token、缓存读取 910,080 token，本局费用 **$0.19054908**。结局输出当刻为 99 次、$0.18638312，差额为收尾后台调用。共享账本在本局关闭时为 $0.678753，其中包含其他并行局，不能当成本局成本。

驱动共记录 31 次启动、行动或掷骰续接的结算返回，合计等待约 520 秒，中位数 15.54 秒，最长 43.87 秒；这些数字包含需要玩家继续选择的中间返回，不等同于完整回合延迟。此次未检查终端排版、图片实际呈现、声音或网络多人体验。

| 回合 | 玩家实际推进 | 已发生结果 |
|---|---|---|
| T1–T3 | 领电报、询问委托、进书房查门窗 | 侦查 60→28，困难成功；发现松窗锁、书架空档与细土 |
| T4–T5 | 查书桌，失败后说明额外搜查办法 | 94 失败，孤注 100 大失败；花瓶碎、扣 5 美元，取得日记 |
| T6–T10 | 查院地、访问守墓人、循足迹到陵墓 | 侦查 16/38 成功；说服 31 失败；找到门缝包书纸 |
| T11–T15 | 试门、再问守墓人、走警方及图书馆渠道 | 石门未开；困难说服 43 失败；图书馆 38 对 20 失败，正常放弃 |
| T16–T18 | 回家备灯、晚饭、书房等到十点 | 出现“午夜过后”的来客事件，未做幸运检定 |
| T19 | 不拦路、不攻击，自报身份并问“能聊聊吗？” | 首次 NPC 回应前已发布结局，此后无法继续交谈 |

**已确认的问题**

**ZHU-01 · P1：第一次问好就终局，关键真相与理智结算被跳过。**

T19 的玩家原话是：“道格拉斯·金博尔先生？……我只是想把人平安的消息带给他。能聊聊吗？”17:33:52 UTC 系统先发布「最后一晚」：“道格拉斯说完，矮身爬进陵墓……封住了入口。”17:33:56 才出现本局首次道格拉斯回应，他仍“立在窗边”，并邀请调查员坐下。17:34:00 KP 接着写他离开、第二天早晨及此后的平静生活，存档已经结束。玩家没有机会追问他为何变成这样、还剩多少时间、如何处理书籍，或回应是否把真相告诉侄子的请求。

结局后核查后台，T19 裁定同时给了 `auto_clues=[c_douglas_truth]` 与 `set_flags=[talked_douglas]`，理由还明确要求“听取真相须损失 1D4 理智、克苏鲁神话 +3%，请引擎结算”。`_spoken()` 正确将这条 NPC 证言留到对方实际说出要点后才取得；该次 NPC 仅寒暄、报平安，没有完整说出真相，`Dialogue.told` 为空。但 `_success()` 已提前写入结束标记，`_check_ending()` 只认该标记便在 NPC 开口前结束。最终存档没有 `c_douglas_truth`、`called_name` 或 `e_hello`，SAN 一直 55，也没有神话增长；复盘把“道格拉斯的自白”列为遗漏。

修复阶段复核勘误：首版将本问题归因为“线索被地点过滤”，这个判断不成立。`module.graph.at_hand()` 原本就允许人物证言随人物移动；用实际结束存档重建世界及逐阶段快照均可验证。真实根因是计划中的证言尚未实际说出，终局标记却先行落地。原始日志与初判记录保留，此处已更正主文归因。

工程定位（基线行号）：[等待人物实际说出](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:2809)、[记录实际证言](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:2283)、[成功标记](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:1775)、[结局检查](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:3201)、[真相线索与理智](/root/code/autokeeper-wt/release/modules/zhuishuren/clues.yaml:137)、[会谈事件](/root/code/autokeeper-wt/release/modules/zhuishuren/story.yaml:95)、[结局条件](/root/code/autokeeper-wt/release/modules/zhuishuren/story.yaml:174)。私有裁定调用 ID 为 `108832288d564dbda34ccd76912b3bac`。线索获得、真相听取、对话完成与终局需要共同满足实际完成条件；首轮试玩阶段仅定位，后续修复与独立新存档复测另记。

**ZHU-02 · P2：设岗立即触发午夜事件，漏过每夜幸运检定。**

T18 玩家明确说“先安静等到夜里十点；有……声音就立即停下来观察”。裁定原文认为“守到十点无异常，具体夜里的动静留给后续轮次”，并仅设置 `watching`。实际 `e_figure` 随标记立即触发：存档中的事件时间为 D1 17:06，正文却说“午夜过后”；该回合结束时钟为 D1 22:10。全局八次可见掷骰中没有幸运检定。

源模组对这个事件明确要求“每晚做一次幸运检定，成功的那一夜”才出现人影，但结构条件只写了 `when: flag:watching`，引擎 `_flags()` 立即 `_fire()`。这是可复核的结构条件与规则文本不一致，而不只是“午夜”措辞有误。[事件定义](/root/code/autokeeper-wt/release/modules/zhuishuren/story.yaml:83)、[标记事件执行](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:2929)。私有裁定调用 ID 为 `d3c6f87afbbe411f9ac4279997459702`。

**ZHU-03 · P2：掷骰前的公开理由泄露尚未发现的事实及 NPC 动机。**

T3 在第一次检查门窗的骰前提示直接显示“查看门窗即可看出窗锁年久松动”。T4 尚未找到日记时，提示显示“日记在旧书桌”。T9 显示“成功可得怪足迹”。T12 显示守墓人“怕惹麻烦、信墓里有鬼”，而人物当时并没有说出这个信念。玩家会在成败尚未结算、人物尚未透露前获知内容。

这些不是驱动自加的资料；根代理已核实真实 RollModal 会显示 `roll_prompt.why`。当前实现从私有 `Ruling.reason` 切取开头，再靠字串过滤决定是否安全，以上换一种说法便漏过。[公开理由构造](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:785)、[过滤](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:807)。试玩没有根据骰前泄露另走捷径；已经提交的搜查与谈话均正常结算。

**ZHU-04 · P2：手稿全文已经展示，叙述仍说册子卡住、尚未取出。**

T4 孤注侦查 100 后，17:22:51 UTC 手稿事件完整给出道格拉斯日记，包括地下朋友和将要加入他们的决定。随后 17:23:18 KP 却说册子还在桌后：“手伸进去够得着，但边上还压着东西，一时抽不出来。”T5 玩家因此又花一轮挪杂物、取出并阅读。这让“已经得到的线索”与“眼前还做不到的事”冲突。

原模组明确允许这次孤注失败仍得到日记、代价是打碎物件，因此“失败仍有所得”本身符合模组。问题是 `_gain()` 已登记并发出全文后，KP 把发现叙述为尚不可读。[手稿发放](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:2874)、[模组特殊失败](/root/code/autokeeper-wt/release/modules/zhuishuren/places.yaml:142)。后台 T4 裁定和复核均给出了 `c_diary`，可以据此检查发放后的叙事一致性。

**ZHU-05 · P2：明确收到或携带的证物没有进入装备台账。**

T7 明确“收好钥匙和日记”，T11 KP 明确把门缝牛皮纸收进口袋。T16 查询人物卡底层状态，物品栏仍只有“奥马利的电报”。T17 玩家特意点清物品，KP 确认“电报、委托信、日记、钥匙、那片牛皮纸都在”。结局存档的 `item_owners` 与世界实体也仍只有电报，没有钥匙、日记或纸片。

真实人物卡会组合世界物品实体与 `inventory` 显示装备，所以这不只是假定的界面问题。[装备显示](/root/code/autokeeper-wt/release/src/autokeeper/tui/modals/sheet.py:150)、[日常物品登记](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:1865)。本局没有再使用这些证物触发失败，故后续规则影响尚未验证；确认的是记录与叙述不一致。

**ZHU-06 · P2：终局的理智恢复奖励没有执行。**

「最后一晚」奖励写明“得知它不会再回来，恢复 1D6 理智”。实际结局没有恢复掷骰或数值变化，最终 SAN 55。`_end.reward()` 仅正则识别“理智 +骰式”，不会匹配本模组的“恢复 1D6 理智”；这与 ZHU-01 的真相损失漏结算是两个不同环节。[奖励解析](/root/code/autokeeper-wt/release/src/autokeeper/engine/game.py:3218)、[模组奖励](/root/code/autokeeper-wt/release/modules/zhuishuren/story.yaml:181)。技能成长需要玩家后续主动操作，本局没有发起，未将未成长报成问题。

**观察及测试工具问题**

守墓人两次交涉都用调查员未专长的说服 10，失败后仍能循其他路径继续；这不构成卡死，且模组确实设置了社交门槛。五金店购物的组合行动没有完成，下一轮明确询问后 KP 才说明已经打烊；T13 的“去警局并问案情”也要到 T14 再说一次。可以改善多步意图的交代，但本报告不把这些直接判成引擎缺陷。

试玩阶段无技术报错或重试。结局后的 `op=debrief` 已生成完整复盘并写入原始记录，但大段 JSON 向 PTY 打印时，驱动 `play.py:45` 抛 `BlockingIOError: [Errno 11] write could not complete without blocking`，进程退出码 1。`finally` 正常保存 `finished=true`、`ended=true`、`pending=false`。这属于临时测试驱动，不能计作 AutoKeeper 游戏崩溃；记录在 `harness-error.txt`。

调查过程的优点是线索有明确空间关系，失败后有代价也有可走的其他路径，托马斯会提供灯、食物与调查协助。最影响本局的部分集中在首次接触真相后的规则和时序：一个礼貌问候同时成为“会谈完成”“真相听取”“封闭出口”的替代条件，使高潮缩成了一轮，也让理智系统在整局里完全没有出现。

**原始记录与复核材料**

- [玩家逐轮输入、骰点、原话、公开提示与完整复盘](/root/code/autokeeper-wt/release/dev/output/parallel-playtest-2026-10-23/zhuishuren/player.jsonl)
- [问题记录及结局后确认条目](/root/code/autokeeper-wt/release/dev/output/parallel-playtest-2026-10-23/zhuishuren/issues.jsonl)
- [最终状态、耗时与费用](/root/code/autokeeper-wt/release/dev/output/parallel-playtest-2026-10-23/zhuishuren/status.json)
- [结局后提取的裁定及存档证据](/root/code/autokeeper-wt/release/dev/output/parallel-playtest-2026-10-23/zhuishuren/engineering-evidence.json)
- [完整私有调试记录，仅结局后读取](/root/code/autokeeper-wt/release/data/games/debug/qa-5h-20261023-zhuishuren.jsonl)
- [结束存档](/root/code/autokeeper-wt/release/data/games/qa-5h-20261023-zhuishuren.json)
- [测试驱动的输出错误](/root/code/autokeeper-wt/release/dev/output/parallel-playtest-2026-10-23/zhuishuren/harness-error.txt)

所有原始记录保留在忽略目录。此次只新增报告和证据提取，未修复引擎、改动模组或修改试玩事实。
