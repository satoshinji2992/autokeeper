# 规则对照表

开发用：规则条目、原文页码、当前实现与状态、对应测试。依据是《克苏鲁的呼唤 第七版 守秘人规则书》中文版（页码为书上印的页码），本机文本在 `data/cache/rules/`（`combat.txt` 第六章、`chase.txt` 第七章，由 `rule/` 下的 PDF 提取）。不进每轮的运行流程，也不增加审批环节。

状态：**符合**＝已按原文实现；**简化**＝做了但与原文有出入；**未做**＝原文有、引擎没有；**错误**＝与原文相反。「重构」指 0.0.4 遭遇引擎重构里的工作包（见 `combat.md`）。

## 检定与主持（第五章 72–82 页、第十章 167–169 页）

| 条目 | 页 | 原文要点 | 当前 | 状态 | 测试 |
|---|---|---|---|---|---|
| 对抗的难度 | 72 | 对活物，难度由对方相应的技能或属性定：低于 50 常规，50 及以上困难，90 及以上极难；对物一般常规，明显超出常规困难，异常困难极难 | 裁定员提示写明这条，并说不因调查员自己技能高而提高难度、准备充分可不掷骰（`agents/cast.ARBITER_SYSTEM`）；行动对着 NPC 时，提示附上对方的相应数值和各自给出的难度（`cast.opposing`，用 `rules/melee.opposed_difficulty`；模组没写的按常人技能基础值），模组门槛另有规定的照门槛 | 符合（提示与上下文） | `test_idea::test_the_opposing_values…`、`test_the_arbiters_prompt_marks…` |
| 关键线索 | 170–171 | 避免让一条隐藏线索成为整场冒险的关键；错失时还有灵感检定；推进情节必需的设为显明线索，发现它不需要掷骰 | 裁定员看到的可得线索标【关键】（`Clue.core`）：没写检定或摆在明处的直接给；要检定的失败了照旧失败，补救靠换办法或灵感检定，不重掷照样适用 | 符合（提示与上下文） | `test_idea::test_the_arbiter_is_told…`、`test_the_arbiters_prompt_marks…` |
| 失败 | 73 | 失败通常不造成伤害或理智损失，可能只是没做成；“最糟的事”留给孤注一掷 | 裁定员提示：普通失败多为没做成，`on_fail` 多数留空，最糟的后果写进 `on_push_fail`；失败照旧失败（`Game._failed`） | 符合（提示） | `test_idea::test_the_arbiter_is_told…`、`test_engine::test_a_failed_roll_stays_failed…` |
| 孤注一掷要说明怎么做 | 73 | 玩家问能否孤注一掷，守秘人总要问“你如何行动”；时间总会流逝；目标必须仍可实现；技能难度通常不变，局势变了可调整 | 界面与联机选孤注一掷时必须写明做法（`PushModal`）；裁定员复核一次（`Cast.repush`，记进回合日志，重试不再问）：做不到则不掷骰、按放弃处理并说明理由；做法的耗时先过去；可换技能、难度和照这个做法的失败后果。AI 调查员行动时连同“检定失败时怎样孤注一掷”一起说（`agents/cast.Proposal.push`），收到孤注一掷的选择时按这个做法走同样的复核（`engine/game.ai_choice`）；没说做法的用幸运或放弃，不再直接重掷。旧存档的 `"push"` 照旧直接重掷 | 符合 | `test_idea::test_a_push_says_how…`、`test_a_push_that_cannot_reach…`、`test_a_push_without_a_way…`、`test_an_ai_players_stated_push…`、`test_net::test_an_ai_seat_pushes…` |
| 揭示预兆 | 74 | 孤注一掷前可说明失败后果 | 选择前显示裁定员写的一般后果（`Ruling.on_push_fail`） | 符合 | `test_tui`（弹窗） |
| 孤注一掷的范围 | 73 | 只有技能和属性检定可孤注一掷；幸运、理智、战斗不可 | `rules/coc.Check.can_push`；灵感检定不提供孤注一掷 | 符合 | `test_rules` |
| 灵感检定 | 82、167–168 | 僵局时由玩家提议；守秘人找出遗漏的最关键线索；智力最高的调查员掷智力；无论成败都拿到线索；罕见而重大 | `engine/idea.py`：Ctrl+L、`/灵感`、菜单、联机座位；线索按“未得到、前置已满足、关键优先、支持离得最近的结论”确定性选出；每个游戏日一次 | 符合 | `test_idea::test_the_most_crucial…`、`test_a_successful_idea_roll…` |
| 灵感检定的难度 | 168 | 从未提及常规；提及但未强调困难（很久以前提及可回到常规）；已挑明或玩家讨论过极难 | 每轮结束从记录统计各线索的出现（`GameState.mentions`）：去过它的地点、见过它的 NPC，或叙述中出现它的物件、NPC、标题算提及；叙述提到 3 次以上或玩家自己的输入提到算强调；“很久以前”取 2 个游戏日 | 简化（启发式） | `test_idea::test_difficulty_follows…` |
| 灵感检定成功 | 168 | 守秘人迂回地把灵感融入故事，而非直接给出 | 线索记入线索本，守秘人得到“写成调查员自己的领悟”的说明 | 符合 | `test_idea::test_a_successful_idea_roll…` |
| 灵感检定失败——千钧一发 | 169 | 线索照给；时间流逝；调查员被置于能意识到的最糟局面；直接切入场面，之后玩家才行动 | 时间过去 3 小时（`idea.LOST`，项目设定）；导演按新的「千钧一发」安排场面（可带调查员去别处、马上发生的事件），守秘人讲切入，之后玩家行动；严格模式没有导演时由守秘人依已有局势讲 | 符合 | `test_idea::test_a_failed_idea_roll…` |
| 守秘人坚持灵感检定 | 169 | 玩家缺少方向时，守秘人应坚持让他们做灵感检定 | 长时间没有进展（`STALL_TURNS`）时提议灵感检定，由玩家决定；提议的那一次不受每日限制；导演不再自行送线索 | 简化（提议而非强制） | `test_idea::test_a_stall_offers…` |

## 主持游戏（第十章，153–188 页）

第十章大多是给人类守秘人的主持建议。这里只列改变了智能体行为的条目：提示里的句子只写要求，页码记在代码注释和这张表里（`skills/keeper-craft`、`skills/coc7-core` 的正文原样进运行时提示）。测试都在 `tests/test_keeper_craft.py`，另注的除外。

| 条目 | 页 | 原文要点 | 当前 | 状态 | 测试 |
|---|---|---|---|---|---|
| 连续的同类检定 | 163 | 同一行动要求连续多次检定会大大降低成功率；三个哨卡用一次成功的检定代替，觉得更难就提高难度 | 裁定员：一件事要连过几道同样的关只掷一次，关多就提高难度；已经掷过的后面同样的关不再另掷（`ARBITER_SYSTEM`） | 符合（提示） | `test_the_chapter_ten_lines…` |
| 社交难度与动机 | 165 | 先按对方相应技能定难度，再按对方的动机提高（邪教徒的例子从常规提到极难）；大多数人用对的方法能说动 | 裁定员：对方有强烈的理由不让步时可以再提高一到两级，写明依据 | 符合（提示） | 同上 |
| 疯狂中孤注一掷失败 | 165 | 调查员疯狂时孤注一掷失败，很可能陷入幻觉 | 孤注一掷复核看到调查员的状态（临时疯狂、不定性疯狂），再失败的后果可以是一段幻觉（`PUSH_SYSTEM`、`Cast.repush`） | 符合（提示） | `test_the_push_review_sees…` |
| 不替别人的调查员做主 | 167 | 任何玩家都不能声明其他调查员的行为 | 行动解析与守秘人安排：玩家替别的调查员说的动作、台词或决定，不记成那位调查员做了（`ACTION_RULES`） | 符合（提示） | `test_the_chapter_ten_lines…` |
| 发放信息：只说确认了什么 | 170、172 | 侦查失败不说“这里什么都没有”，只说没找到；成功而这里确实没有，才确认没有 | 守秘人：检定失败时写没有找到，不写成这里什么都没有（`keeper-craft`）；裁定员已有“点名要的检定成功了也可以没有收获” | 符合（提示） | 同上 |
| 生死不替玩家下结论 | 170、175 | 怪物中枪后只说看不出生命迹象；NPC 是活着、濒死还是死了，要急救或医学检定后才告知 | 守秘人：倒下的人或怪物写看得见的样子，是死是活除非检查过或一望可知，不下结论 | 符合（提示） | 同上 |
| 幸运检定 | 173 | 情境更依赖外部环境而非调查员的行动时，用幸运检定 | `coc7-core`（裁定员读的规则摘要）写明；`rules/coc.Investigator.value("幸运")` | 符合（提示） | 同上 |
| 多项洞察技能 | 172 | 侦查、心理学都能察觉同一件事时，掷一次，与较高的那项比 | `coc7-core`：只掷一次，用调查员较高的那一项 | 符合（提示） | 同上 |
| 展现神话的恐怖 | 175–177、179 | 少即是多，描述模糊、点出异界的特性，让玩家用想象补全；神话生物不能轻易被认出，近看也只是“浮出水面的黑暗潮湿的形体”；气味、痕迹；日常事物的反常不加解释 | 守秘人：先写感官，不说名字和来历，不解释不合理化，认出以前用系统给的叫法（`keeper-craft`）；事件让调查员看见还没认出的怪物或没见过的人时，报告给出玩家的叫法（`Game._unknown`，`Creature.called`、`NPC.called`、`GameState.identified`）；怪物的大场景用叫法写到也触发（`Showcases.named`） | 符合（提示与上下文） | `test_the_keeper_shows_a_horror…`、`test_a_horror_an_event_shows…`、`test_a_creatures_big_picture…` |
| 失败的理智检定 | 129–130、178 | 理智检定失败总让调查员一瞬间失去控制，守秘人决定一个不由自主的动作（惊叫、呆立、畏缩） | 守秘人：写一下不由自主的反应，只到这一瞬，不引出没有结算的后果，之后由玩家决定；重放评测不把它算作“代做”（`engine/check.RULES`） | 简化（不掉落物品、不在战斗中代替行动） | `test_the_chapter_ten_lines…` |
| NPC 不抢调查员的戏 | 156、182 | 不要让 NPC 掩盖调查员的光辉；解决问题的责任落在调查员身上 | 守秘人安排在场的人主动参与时，帮忙只做力所能及的一步，不替调查员查明真相、做关键决定（`PLAN_SYSTEM`） | 符合（提示） | 同上 |

已经覆盖、没有新增的：亲自扮演 NPC 而不是转述（158，`KEEPER_SYSTEM` 第 8 条）；让出场过的 NPC 回来（159，`keeper-craft`）；在角色所知界限内扮演（160，`NPC_SYSTEM`）；“好的，而且／好的，但是”（167、170，裁定员的“可即兴”与导演的展开）；只描述证据、推断留给玩家（169，`KEEPER_SYSTEM` 第 8 条）；关键线索不只一条路（170，编译期线索网）；显明线索不用掷（170，上面一节）；灵感检定（167–169，上面一节）；孤注一掷要说明做法、失败更糟、事先预告（164–165，上面一节）；与职业直接相关的普通行为自动成功（163，`coc7-core`）；合理警告（177–178，导演的“交代可察觉的征兆与应对空间”和遭遇引擎的危险提示）；结语（181，`engine/debrief.epilogue`）；重要 NPC 的幸运池（169，遭遇引擎）。

有意不采纳的：

- 按时长划分游戏、分幕、在一幕末尾留钩子、快进到最终场景（166、180–181）：AI 跑团没有团次结构。
- 根据玩家反应加大或放松威胁（166）、为了不让玩家出局而不急于制造死亡（181）：与“失败、死亡与疯狂都允许，风险以模组与局势为准”相冲突。
- 检定失败时结果可以部分或完全实现、孤注一掷失败后让目标以代价达成（162–163 的例子）：与“失败就是失败”相冲突。
- 玩家通过检定后自己叙述结果、追逐中幸运检定成功的玩家自己描述地点（163–164、175）：本项目由守秘人叙述，玩家的断言不成为事实。
- 出色的角色扮演可降低难度（159）：裁定员看不到玩家原话，修辞不改变难度。
- 守秘人看到玩家没方向时大量即兴新情节（166、185 的“即兴发挥”）：导演克制，只在模组没写到时补局面，不编剧情。
- 心理学失败时可以随意给出真假难辨的信息（172）：守秘人只能说系统允许的外在表现，不编造误导。
- 首次杀人的理智检定（166，可选规则）、幻觉取材于背景故事（179）、背景条目被疯狂扭曲（161–162）：属于疯狂与背景的规则层，留给 0.1.0 的疯狂工作。
- 环境危险落到调查员身上时总给一次检定（175）：导演事件的伤害现在直接结算，要做需要引擎支持“事件伤害先给闪避或侦查”，待定。
- 避免与非人的智慧对手谈判、智慧神话生物遇险会退却（177）：属于遭遇引擎里怪物的行为，交给 WP-C。

## 战斗（第六章，85–108 页）

| 条目 | 页 | 原文要点 | 当前 | 状态 | 测试 |
|---|---|---|---|---|---|
| 行动顺序 | 86 | 敏捷从高到低；平手时战斗技能高者先 | `rules/combat.turn_order`、`order`：敏捷，平手时战斗技能高者，再平手调查员先（项目设定） | 符合 | `test_combat_rules::test_a_readied_gun_counts_only…` |
| 准备好的枪 | 95 | 准备射击时按敏捷 +50 | `rules/firearms.readied_bonus`、`combat.order(firing=…)`：只给本轮要开火、有子弹、没卡壳的枪 +50；旧引擎按这一轮宣告开枪的人传 `firing`，打拳不再 +50 | 符合 | `test_rules_firearms::test_a_readied_gun_goes_first…`、`test_combat_rules` |
| 推迟行动 | 86 | 可推迟到另一角色之后；全部推迟则本轮无人行动 | 无 | 未做 | 重构 |
| 每轮行动 | 86 | 攻击、战技、脱离、施法、其他耗时动作；多次攻击的怪物全在自己的回合内打完 | 引擎（`engine/encounter`）每人每轮在自己的敏捷位行动一次；多次攻击的怪物在自己的位上把每次攻击都打完（`actions.picks`：安排写得少的，余下几次打同一个目标） | 符合 | test_encounter_fight::test_npcs_fight_back_and_a_creature_with_three_attacks_makes_them_all |
| 反击 | 86–87 | 格斗对抗；成功等级高者伤对方；平手攻击者胜；都失败无人受伤 | `rules/melee.melee_outcome`；旧引擎 `rules/combat.melee` | 符合 | `test_rules_melee::test_every_pair_of_levels…`、`test_melee_both_fail_nobody_hits` |
| 闪避 | 87 | 平手闪避者胜；都失败无人受伤 | 同上 | 符合 | 同上 |
| 极难伤害与贯穿 | 87 | 只在攻击者自己回合的攻击中；钝器取伤害与伤害加值最大，贯穿再加一份武器骰；反击不能 | `rules/melee.damage_mode`、`damage_plan`：只有攻击者自己回合；钝器取最大，贯穿再加一份武器骰（含武器常数，不含伤害加值）；反击胜出只造成普通伤害。旧引擎 `combat.melee` 已同步改正 | 符合 | `test_rules_melee::test_extreme_damage_only…`、`test_damage_of_a_blunt…`、`test_combat_rules::test_fighting_back_never…` |
| 不可孤注一掷 | 87 | 战斗检定不能孤注一掷 | 战斗中不提供 | 符合 | — |
| NPC 的应对 | 87 | NPC 与怪物默认反击，只在准备撤退时闪避 | `rules/melee.keeper_default`；引擎里守秘人的人按安排的 `react` 应对，没安排或不合用时反击（开枪时不应对，不替他扑向掩体） | 符合 | test_encounter_fight::test_npcs_fight_back_and_a_creature_with_three_attacks_makes_them_all |
| 持械与临时武器 | 87–88 | 基础武器用斗殴；临时武器比照武器表定伤害与技能 | 只能用手里或身上真有的武器（攻击 id）；临时抄起的东西先经 `Bring` 登记进手里（`DeclaredAction.takes`），再按最像的武器结算；武器表里没有的东西交裁定员按 `other` 裁定伤害 | 符合 | test_encounter_fight::test_a_weapon_picked_up_is_registered_before_it_is_used |
| 武器技能基础值 | 87 | 剑 20% 等，见武器表 | `rules/catalog.SKILL_BASE`：斧 15、剑 20、连枷 10、矛 20、鞭 5、绞索 15、链锯 10、冲锋枪 15、弓 15、重武器 10、机枪 10、火焰喷射器 10；`格斗(刀剑)` 是 `格斗(剑)` 的别名 | 符合 | `test_combat_rules::test_weapon_skills_start_at_the_books_base…` |
| 战技与体格 | 89 | 对方体格每大 1 一颗惩罚骰，最多 2；大 3 以上无效；对方可闪避或反击 | `rules/melee.maneuver_feasible`；旧引擎 `combat.melee` | 符合 | `test_rules_melee::test_a_manoeuvre_against_a_bigger_target` |
| 战技的效果 | 89 | 缴械或夺物；持续劣势（惩罚骰或盟友奖励骰）；挣脱压制；推倒、推下 | 缴械让武器掉在原地、夺取落到攻击者手里（`effects.Drop`）；天生的爪牙夺不走；推倒：倒地并失去下一次行动；制住：被制住的人在自己的位上只能用力量挣脱，盟友打他得奖励骰（`melee.disadvantage_bonus`） | 简化（夺物以外的持续劣势只做了被制住） | test_encounter_fight::test_disarming_drops_the_real_weapon…、test_world_life::test_claws_cannot_be_disarmed |
| 突袭 | 90–91 | 聆听/侦查对潜行定难度；没准备好的目标自动命中（大失败除外）或给奖励骰；远程攻击总要掷骰 | 规则层同前；引擎：被偷袭一方第一轮不能应对，近战除大失败外命中，远程照常掷；`EncounterStart.surprise_contest` 由引擎掷对抗 | 符合 | test_fight::test_an_event_that_scripts_an_attack… |
| 寡不敌众 | 91 | 本轮已闪避或反击过后，之后的近战攻击得一颗奖励骰；多次攻击的怪物要应对同样次数后才算；不适用于远程 | `rules/melee.outnumbered_bonus`；引擎按本轮已应对次数（多次攻击的按其攻击次数）给后来的近战攻击一颗奖励骰，远程不给 | 符合 | test_encounter_fight::test_outnumbered_a_defender_who_has_answered… |
| 投掷武器 | 91 | 可闪避；不能反击，除非在敏捷五分之一英尺内；加一半伤害加值 | `rules/melee.reactions_against`；引擎按攻击种类给应对选项：投掷只能闪避，开枪只能扑向掩体或不应对 | 符合 | test_rules_melee::test_what_a_throw… |
| 脱离近战 | 91 | 花一个回合脱离，前提是有路且未被压制 | 逃跑在自己的位上脱离（被制住不行），轮末先问守秘人的人追不追（安排里写明的照办，明说不追就不追），再判胜负后果 | 符合 | test_encounter_fight::test_an_explicit_let_go_is_obeyed…、test_an_undecided_pursuer_is_asked… |
| 护甲 | 91 | 从伤害中减去；对魔法、毒、溺水无效 | `rules/wounds.apply_wound`：先扣护甲；毒、溺水窒息、魔法不扣；摔落、火焰按表 III 由守秘人定，不扣 | 符合 | `test_rules_wounds::test_armour_first…` |
| 射击难度 | 95 | 基础射程常规，两倍困难，四倍极难；超射程只有大成功贯穿 | `rules/firearms.range_band`、`shot_difficulty`、`ShotModifiers.impale_needs`：基础/两倍/四倍射程，超射程只有大成功贯穿；霰弹枪各档都是常规难度、按档降伤害（54 页）。引擎：守秘人在遭遇计划里填两人间的距离（`EncounterTurnPlan.distances`，记进 `FightScene.distance`），开枪按距离定档，超过四倍射程打不着；没填距离按基础射程 | 符合 | `test_rules_firearms::test_range_bands…`、`test_a_shotgun_keeps…`、`test_encounter_perception::test_the_range_band_follows…` |
| 寻找掩体 | 95 | 闪避检定，成功则射手一颗惩罚骰；无论成败失去下一次攻击 | `rules/firearms.dive_for_cover`；引擎：扑向掩体掷闪避，成功射手一颗惩罚骰，无论成败失去下一次行动（`RoundBudget.lost_next`） | 符合 | test_encounter_fight::test_an_opening_blow_keeps_the_weapon_it_named |
| 部分遮蔽 | 95 | 一半以上被遮住，一颗惩罚骰 | `rules/firearms.shot_modifiers`（`partial_cover`）；引擎：扑向掩体成功的人记一处半遮的掩体（`FightScene.cover`，`partial`），守秘人也可在计划里写（`CoverNote`，`full=False`），之后朝他开枪一颗惩罚骰 | 符合 | `test_encounter_perception::test_a_dive_leaves_the_target_half_hidden…` |
| 抵近射击 | 95 | 敏捷五分之一英尺内一颗奖励骰；目标不能闪避或反击 | `rules/firearms.point_blank`、`shot_modifiers`；引擎：射手与目标扭打在一起，或守秘人填的距离在射手敏捷五分之一英尺内，算抵近 | 符合 | test_rules_firearms::test_point_blank_and_volleys |
| 瞄准 | 95 | 花一回合瞄准，下回合射击一颗奖励骰 | `rules/firearms.shot_modifiers`（`aimed`）；引擎：行动 `aim`（界面「瞄准 X」），他的下一次掷骰（一般就是下一枪）一颗奖励骰（`Edge`，`until="next_roll"`） | 符合 | `test_encounter_perception::test_aiming_a_round…` |
| 高速目标、目标体格 | 96 | MOV8 以上全速移动一颗惩罚骰；体格 −2 以下惩罚骰，4 以上奖励骰 | `rules/firearms.shot_modifiers`（`fast_target`、`target_build`）；引擎：目标这一轮或上一轮宣告逃跑、奔跑、追赶（守秘人的人看它这一轮排在前面能做的安排）且 MOV 8 以上算全速；体格取目标人物卡 | 符合 | `test_encounter_perception::test_a_man_running_flat_out…`、`test_a_big_target…` |
| 装填 | 96 | 一轮装两发；换弹夹一轮；装一发并开火有惩罚骰 | `rules/firearms.reload`；引擎装填按两发一轮、弹匣一轮，不超过备弹（`WeaponState.spare`，未知时不限） | 符合 | test_rules_firearms::test_reloading_and_ammunition |
| 手枪连射 | 96 | 一轮 2–3 发，每发各掷一次，各一颗惩罚骰 | `rules/firearms.shots_allowed`、`shot_modifiers`；引擎按 `AttackPick.shots` 逐发掷，每发都带惩罚骰 | 符合 | test_rules_firearms::test_point_blank_and_volleys |
| 射击近战中的目标 | 96 | 一颗惩罚骰；大失败打中队友 | `rules/firearms.shot_modifiers`（`into_melee`）、`fumble_hits`；引擎：扭打关系跨轮保留（`FightScene.engaged`），朝与己方扭打的目标开枪一颗惩罚骰；大失败打中正和目标扭打的己方中幸运最低的一个 | 符合 | test_encounter_fight::test_shooting_into_a_melee_takes_a_penalty_die、`test_encounter_perception::test_a_big_target…` |
| NPC 看得见谁 | 87、119 | 守秘人知道一切，但 NPC 只按自己看见的行动；躲起来的人对方侦查不成功就找不到 | 引擎（项目，见 `combat.md` 守秘人全知）：守秘人的人看不见躲在全遮掩体后（`CoverNote` `full=True`）、战斗中躲藏成功（潜行，攻击即暴露）、旧追逐里躲起来、或身处黑暗且不带光源也没贴身扭打的人（怪物不受黑暗限制）；遭遇简报写明各人看得见谁、听说过谁（世界记录里这个 NPC 知道的事实）；计划让他攻击看不见的人，检查不通过（`plan_check`） | 符合 | `test_encounter_perception::test_a_position_the_keeper_declares…`、`test_a_hide_that_works…`、`test_the_dark_hides…`、`test_someone_hidden_in_the_old_chase…`、`test_what_an_npc_was_told…` |
| 自动武器 | 96–98 | 弹幕、短点射、逐组惩罚骰 | 不在规则层：交守秘人按 `other` 裁定（`rules/firearms` 文档写明） | 未做 | — |
| 故障 | 98 | 出目不低于故障值就没开火；转轮、中折、手动枪机只是哑弹；杠杆式卡壳，修理 1D6 轮并需机械维修或射击检定 | `rules/firearms.malfunction`；引擎：转轮、中折、手动枪机只是哑弹（耗一发），杠杆式等卡壳（`effects.Jam`）；排除卡壳是一轮一次机械维修或射击检定（书上先要 1D6 轮，项目简化） | 简化（排除卡壳的轮数） | test_world_life::test_rounds_fired_and_a_jam…、test_a_revolver_that_misfires_only_had_a_dud |
| 弹药与卡壳的延续 | — | 打空、卡壳要延续到下一场 | 枪是物件（`Entity.weapon`），每次开火、卡壳、装填都经 `engine/effects` 写进世界，下一场照旧 | 符合 | `test_world_life::test_rounds_fired_and_a_jam_carry_into_the_next_fight` |

## 伤害与治疗（第六章，100–103 页）

| 条目 | 页 | 原文要点 | 当前 | 状态 | 测试 |
|---|---|---|---|---|---|
| 轻伤与重伤 | 100 | 单次伤害不到最大生命一半为轻伤，达到一半为重伤 | `rules/wounds.apply_wound`（`Rules.damage` 委托给它） | 符合 | `test_rules`、`test_rules_wounds::test_a_minor_wound_and_a_major_one` |
| 当场死亡 | 100 | 单次伤害**大于**最大生命值才死；恰好等于是重伤 | `rules/wounds.apply_wound`：单次伤害大于最大生命必死，不论剩余生命 | 符合 | `test_rules_wounds::test_exactly_the_maximum…`、`test_rules::test_a_blow_of_exactly_the_maximum…` |
| 重伤后 | 100 | 立刻倒地；过体质，失败昏迷 | `rules/wounds.apply_wound`；引擎把重伤的人记为倒地（`FightScene.prone`） | 符合 | test_rules_wounds::test_a_minor_wound_and_a_major_one |
| 生命归零 | 100 | 只受过轻伤：昏迷，会随时间恢复；有重伤：濒死 | `rules/wounds.apply_wound`：轻伤昏迷；有重伤濒死并昏迷；归零后的伤害忽略，蓄意了结则死亡 | 符合 | `test_rules_wounds::test_at_zero_hit_points…`、`test_damage_at_zero…` |
| 急救 | 100 | 一小时内有效，回复 1 点；只能尝试一次，之后是孤注一掷；两人可一起做 | `rules/wounds.first_aid`；引擎：急救在自己的位上掷，成功 +1 并唤醒、濒死者只稳定，每处伤只一次（失败也算） | 符合 | test_encounter_fight::test_first_aid_wakes_the_unconscious_and_ticks_the_skill |
| 医学 | 101 | 至少一小时；不在当天要困难；回复 1D3；濒死者须先急救稳定 | `rules/wounds.medicine`、`medicine_difficulty`：+1D3，当天常规、之后困难；濒死者须先急救稳定；每处伤一次成功（`aided` 里记 `medicine:<id>`） | 符合（规则层） | `test_rules_wounds::test_medicine_takes…`、`test_first_aid_stabilizes…` |
| 濒死 | 101 | 下一轮结束起每轮过体质，失败即死；只有急救能稳定，得 1 点临时生命；稳定后每小时过体质，之后须医学 | `rules/wounds.dying_roll_due` 等；引擎：濒死从开始濒死那一轮的下一轮末起每轮过体质，失败即死；倒下的调查员不冻结，按“继续”一轮一轮打下去 | 符合 | test_encounter_fight::test_a_dying_investigator_rolls_con_from_the_end_of_the_next_round… |
| 轻伤恢复 | 101 | 每天 1 点 | `rules/wounds.heal_day` | 符合（规则层）；时间推进接入待定 | `test_rules_wounds::test_natural_healing` |
| 重伤恢复 | 101–102 | 每周体质检定回复 1D3，极难 2D3；静养、医疗加奖励骰；极难或回到一半以上时去掉重伤 | `rules/wounds.heal_week`、`week_dice`、`week_bonus` | 符合（规则层）；时间推进接入待定 | `test_rules_wounds::test_natural_healing` |
| 其他伤害 | 102–103 | 表 III：轻度 1D3、中度 1D6、重度 1D10、致命 2D10、终结 4D10、血肉横飞 8D10；窒息与溺水每轮过体质；毒药极难成功减半 | `rules/wounds.OTHER_DAMAGE`（表 III）、`harm_dice`、`asphyxia_round`（困难若在剧烈活动，失败后每轮受伤，归零即死）、`poison_damage`（极难减半）；裁定员的提示附表 III（`agents/encounter.OTHER_DAMAGE`），遭遇里的伤害骰写成 `DiceText`（`Ruling.effects`）；建了规则书索引时可按需查原文（`rules_lookup`） | 符合 | `test_rules_wounds::test_suffocating_to_zero…`、`test_encounter_plan_agent`、`test_rules_lookup` |
| 伤势的延续 | — | 同一个人物、怪物跨场景、跨战斗保持生命值与状态；同一次伤害只扣一次 | NPC 与怪物的身体记在世界（`Entity.life`），怪物入战即成为世界实体；所有伤害经 `engine/effects`（调用 `rules/wounds`），每次带 injury id，重复的被拒绝（插件与引擎不会重复扣） | 符合 | `test_world_life::test_a_dead_creature_stays_dead`、`test_a_hurt_npc_comes_back_hurt`，`test_effects::test_a_plugin_cannot_take_off…` |
| 枪伤减半与护甲 | 256、258、274 | 食尸鬼“枪械和抛射武器造成掷出的伤害的一半”；飞水螅先取最低伤害再扣护甲 | `rules/wounds.apply_wound`：先把掷出的枪伤减半（向下取整），再扣护甲 | 符合（按怪物条目推定的顺序） | `test_rules_wounds::test_guns_halved_before_the_armour` |
| 击晕（可选） | 105 | 钝器战技成功，目标昏迷并只受 1 点伤害 | `rules/wounds.knockout_blow`；模组写的“只被拳头打 X 点就击晕”另由 `Body.knockout` 处理 | 符合（规则层） | `test_rules_wounds::test_a_knockout_from_fists` |

## 追逐（第七章，111–128 页）

| 条目 | 页 | 原文要点 | 当前 | 状态 | 测试 |
|---|---|---|---|---|---|
| 速度检定 | 112 | 体质（驾驶用汽车驾驶）；极难 MOV+1，失败 MOV−1 | `rules/chase.speed_after`（旧引擎 `speed_roll` 用它）；MOV 不低于 1 是项目设定 | 符合 | `test_rules_chase::test_the_speed_roll…` |
| 直接甩开 | 112 | 逃离者调整后 MOV 高于追逐者就逃走，追逐不开始 | `rules/chase.escapes_at_once`、`placement` | 符合 | `test_rules_chase::test_the_speed_roll…` |
| 起始距离 | 112 | 追逐者落后两个地点；特殊情况可设为一个；不要超过两个 | `ChaseRules.start_gap` 2（1 到 2） | 符合 | `test_rules_chase::test_placing_three_cultists…` |
| 地点 | 112 | 地点是叙事单位，不是等长距离；险境与障碍放在地点之间 | 路线段 | 符合 | — |
| 行动顺序 | 113 | 按敏捷；平手时敏捷对抗 | `rules/chase.chase_order`：敏捷，平手按敏捷对抗的成功等级 | 符合（规则层）；接入待 WP-D | `test_rules_chase::test_monsters_joiners…` |
| 行动点 | 113 | 每人 1 点，MOV 每比全场最慢者高 1 多 1 点 | `rules/chase.action_points` | 符合 | `test_rules_chase::test_action_points…` |
| 可做的事 | 113 | 前进、攻击、施法、撬锁等耗时动作；可推迟；多次攻击的怪物在自己回合全打完 | 只有奔跑、躲藏、设路障、回身 | 简化 | 重构 WP-D |
| 不可孤注一掷 | 113 | 追逐中不能，可下轮再试 | 同 | 符合 | — |
| 险境 | 114 | 谨慎通过：花 1 或 2 行动点换 1 或 2 颗奖励骰；失败由守秘人定伤害（表 III），并失去 1D3 行动点；无论成败都到下一地点 | `rules/chase.careful`、`hazard`：花 1–2 行动点换奖励骰；失败守秘人定伤害并失去 1D3 行动点，仍到下一地点。模组路线的险境写几种办法（`methods`）和按表 III 的伤害骰式（`harm`），矮墙、矮栅栏按原文算险境 | 符合（规则层与模组数据）；接入待 WP-D | `test_rules_chase::test_hazards_and_barriers`、`test_fight_module` |
| 耽搁跨轮 | 114 | 本轮行动点不够扣时，欠下的从下一轮扣（农夫例） | `rules/chase.spend`、`start_round`：不够扣的行动点记作欠下，从下一轮扣 | 符合（规则层）；接入待 WP-D | `test_rules_chase` |
| 障碍 | 115 | 越过前挡住去路；失败通常只是无法前进；可多种方法 | `rules/chase.barrier`：失败不前进；可开路的办法成功后后来者直接通过；每试一次花 1 行动点（项目设定）。模组路线写多种办法（`methods`，打开后后面的人直接过的写 `opens`） | 符合 | `test_rules_chase::test_hazards_and_barriers`、`test_fight_module` |
| 破坏障碍 | 115–116 | 打出伤害抵掉耐久（室内门 5、后门 10、外门 15、砖墙 25…）；打穿后残骸可成险境 | `rules/chase.barrier_hit`、`BARRIER_HP`（室内门/薄围栏 5、后门 10、外门 15、砖墙 25、大树 50、桥墩 100）；模组路线按耐久表写 `hp`（自带模组已写，`check` 提醒没写的门和墙） | 符合（规则层与模组数据）；接入待 WP-D | `test_rules_chase`、`test_fight_module` |
| 追逐中的攻击 | 116 | 只能打同一地点的目标（有枪除外）；花 1 行动点；受每轮攻击次数限制；按普通战斗处理；无论剩不剩行动点，被攻击时总能应对 | `rules/chase.attack_problems`：同一地点（有枪除外）、1 行动点、每轮攻击次数上限；被攻击时总能应对。旧引擎里选开枪仍会变成奔跑 | 符合（规则层）；接入待 WP-D | `test_rules_chase` |
| 追逐中的战技 | 116 | 成功则目标如同险境失败：失去 1D3 行动点，可能受伤；或照战斗中的战技目的生效 | `ChaseRules.maneuver_loss` 1D3，按险境失败结算（`hazard`） | 符合（规则层）；接入待 WP-D | `test_rules_chase` |
| 择路而逃 | 117 | 逃离者可选岔路，守秘人应常给岔路 | 模组路线有岔路和出口（`links`、`exit`），各地点的路线按段 id 连成一张图、段落在真实地点里（`place`；自带模组已写，`graph.route_edges`）；引擎仍走一条直线 | 未做（数据已备） | 重构 WP-D；`test_fight_module` |
| 随机险境 | 117 | 1D100：60+ 普通，85+ 困难，96+ 极难 | `rules/chase.random_hazard` | 符合（规则层，可选） | `test_rules_chase` |
| 突发险境 | 117–118 | 团体幸运检定，玩家与守秘人轮流放置 | 无 | 未做（可选） | — |
| 跟丢 | 119 | 守秘人判定在哪里跟丢：例如逃出视线后追逐者过追踪检定 | `rules/chase.out_of_sight`、`lost_track`：领先两个地点以上或隔着挡视线的地点（项目设定 `sight_gap`）后，追逐者过追踪（或侦查），失败就跟丢 | 简化（规则层）；接入待 WP-D | `test_rules_chase::test_hiding_and_losing_track` |
| 躲藏 | 119 | **任何时候**都可尝试；潜行难度视情况，领先多可给奖励骰；追逐者侦查不成功则逃脱 | `rules/chase.hide_difficulty`、`hide_bonus`、`hidden`：任何时候；潜行难度按追逐者侦查定，领先多给奖励骰（项目设定 `hide_far`） | 符合（规则层）；旧引擎仍要领先两段 | 同上 |
| 追逐中开枪 | 120 | 停下射击：花 1 行动点不前进，按战斗轮射击；边跑边射：射击加一颗惩罚骰，不花行动点 | `rules/chase.shot_cost`：停下 1 行动点不前进；边跑边射一颗惩罚骰不花行动点（`firearms.Shot.moving`） | 符合（规则层）；接入待 WP-D | `test_rules_chase::test_attacks_and_shots…` |
| 另一种方式处理地点 | 120 | 玩家可以用意料之外的方式利用地点（改去开门等） | 交给裁定员 | 简化 | 重构 |
| 中途加入 | 120 | 做速度检定后放到合适地点；比最慢逃离者还慢的追逐者可忽略；重算行动点 | `rules/chase.joins`：比最慢逃离者还慢的追逐者忽略；之后按新的最慢者重算行动点 | 符合（规则层）；接入待 WP-D | `test_rules_chase::test_monsters_joiners…` |
| 更换移动方式 | 120–121 | 游泳等无数据时按 MOV 一半 | `rules/chase.other_movement`：无数据按 MOV 一半 | 符合（规则层） | 同上 |
| 人为险境 | 121 | 闩门 1 行动点免检定；用锁匠锁门一整轮加检定；推衣柜挡门力量检定加 1 行动点；喊人帮忙用话术或恐吓 | `rules/chase.CREATED`：闩门 1 行动点免检定、锁匠锁门一整轮加检定、推重物挡门力量加 1 行动点、掀摊子 1 行动点、开枪惊散人群幸运、喊人帮忙话术或恐吓 | 符合（规则层）；接入待 WP-D | 同上 |
| 分头行动 | 121 | 走不同路线就另记一场追逐 | 不支持（用户决定第一版不做） | 简化 | — |
| 怪物的技能 | 121 | 擅长用敏捷，平常用敏捷一半，不擅长用五分之一；侦查可按智力折算 | `rules/chase.monster_skill`：擅长用敏捷，平常一半，不擅长五分之一 | 符合（规则层）；`engine/encounter/profiles` 读怪物卡，缺的技能用技能表基础值，读不懂的特性列进 `unread`；按擅长程度折算接入待 WP-C/D | `test_combatant_profiles` |
| 多人追逐的摆放 | 124 | 最慢追逐者在起点，其余按 MOV 差向前；最慢逃离者在最快追逐者前两个地点，其余按差值向前；比最快追逐者快的逃离者可直接逃走 | `rules/chase.placement` | 符合（规则层）；接入待 WP-D | `test_rules_chase::test_placing_three_cultists…` |
| 载具 | 115–123 | 猛踩油门、乘客、交通事故、载具速度表 | 无 | 未做 | 以后 |

## 怎么用这张表

- 改规则代码时同步改这张表的「当前」「状态」「测试」三栏。
- 测试名写进「测试」栏；状态写「符合」的条目必须有测试。
- 规则书没写、引擎自己定的数值（例如追逐里的 `LOST`），在代码里标「待核验」或「项目简化」，并在这里列为「简化」。
