"""How exactly a replayed turn did what the engine asked (eval/control.py), and two prompt variants
set against each other turn by turn (eval/session.compare)."""

from __future__ import annotations

import pytest

from autokeeper.eval import control, session
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE


@pytest.mark.parametrize("text, said", [
    ("好，我陪你一起去。", ["accompany"]),
    ("走之前说清楚：我不能跟你去。", []),
    ("（把手电递给你）拿着吧，路上黑。", ["items"]),
    ("这东西我不给你。", []),
    ("（转身离开了门廊）", ["leave"]),
    ("我先走了，明天再说。", ["leave"]),
    ("说实在的，我也不清楚。", []),
])  # fmt: skip
def test_what_an_npc_says_it_does_now(text, said):
    assert control.cues(text) == said


def test_why_a_scene_or_a_reply_went_back():
    assert control.reason("阿萨塔原来在主街，scene 里没有他，也没写去向：还在就写进 here", control.SCENE_REASONS) == "漏写在场的人"
    assert control.reason("托马斯在书房，到墓地要 15 分钟，这一轮只过了 5 分钟，赶不到：", control.SCENE_REASONS) == "来人赶不到"
    assert control.reason("「铁锹」不是登记过的物件：日常小东西写进 things。", control.SCENE_REASONS) == "物件没登记"
    assert control.reason("欺骗说明必须逐字对应本次实际台词", control.NPC_REASONS) == "欺骗说明"
    assert control.reason("说不清的毛病", control.SCENE_REASONS) == "其他"


def test_a_turn_counted_from_what_the_checks_said():
    m = Module.load(EXAMPLE)
    w = control.Watch()
    w.tellings = [{"retry": False, "held": False, "scene": {"here": []}, "shown": False},
                  {"retry": True, "held": True, "scene": {"follow": ["老守钟人"], "left": [{"who": "布道者", "to": "广场"}],
                                                          "things": [{"name": "提灯", "holder": "warden"}]}, "shown": False}]
    w.replies = [{"npc": "warden", "checks": [["欺骗说明必须逐字对应本次实际台词"], []], "offered": ["s1"],
                  "fields": {"accompany": "", "items": [], "leave": "", "played": [], "feigned": []},
                  "text": "好，我陪你一起去。（把提灯递给你）"},
                 {"npc": "preacher", "checks": [[]], "offered": [], "text": "（转身离开了）",
                  "fields": {"accompany": "", "items": [], "leave": "square", "played": [], "feigned": []}}]
    w.unsettled = [{"npc": "warden", "problems": ["当场的动作没有通过校验"]}]
    w.clues = {"c_gears": "", "c_nothing": "不存在", "c_notice": "早已找到"}
    got = w.result(m, [{"data": {"phase": "rejected", "problems": ["阿萨塔原来在主街，scene 里没有他"]}}])
    assert got["scene"]["first"] == "sent_back" and got["scene"]["second"] == "held" and got["scene"]["reasons"] == ["漏写在场的人"]
    assert got["npc"]["reasks"] == 1 and got["npc"]["unsettled"] == 1 and got["npc"]["reasons"] == ["欺骗说明", "当场动作"]
    f = got["fields"]
    assert f["said_missing"] == ["warden:accompany", "warden:items"]  # said it, filled neither
    assert f["scene_vs_reply"] == ["warden:follow", "warden:things"]  # the preacher filled leave: no mismatch
    assert f["meetings_offered"] == 1 and f["meetings_marked"] == 0
    assert got["arbiter"] == {"invalid_clues": {"c_nothing": "不存在"}, "found_again": ["c_notice"]}
    assert control.Watch().result(m)["scene"]["first"] == ""  # nothing told with a scene


def test_a_clue_told_without_being_found():
    m = Module.load(EXAMPLE)
    gears = next(c.text for c in m.clues if c.id == "c_gears")
    told = f"你凑近看：{gears}"
    got = control.clue_slips(m, [], told, "", missed=["c_gears"])
    assert got == {"missed_told": ["c_gears"], "early_told": ["c_gears"]}
    assert control.clue_slips(m, ["c_gears"], told, "", missed=[]) == {"missed_told": [], "early_told": []}  # found: told
    assert control.clue_slips(m, [], told, gears, missed=["c_gears"])["early_told"] == []  # already public
    notice = next(c.text for c in m.clues if c.id == "c_notice")
    assert control.clue_slips(m, [], notice, "", missed=[])["early_told"] == []  # needs no check: anyone may tell it
    diary = next(c.text for c in m.clues if c.id == "c_diary")
    assert control.clue_slips(m, [], f"老守钟人：{diary}", "", missed=[])["early_told"] == ["c_diary"]  # behind his gate


RECORD = """时间：D1 17:21（傍晚），这一轮经过 5 分钟
调查员所在：金博尔家（同一处的其他房间：道格拉斯的书房）
此刻就在调查员身边的人：没有别人
同一处别的房间里、不在眼前的人：托马斯·金博尔在道格拉斯的书房
和调查员同行的人：没有
在别处的人：阿萨塔在主街午餐柜台、梅洛迪亚斯·杰弗逊在公墓
这一轮结算的结果：
- dice：侦查 45 → 93 失败 ✗
裁定员对这一轮的说明（能有什么结果）：窗外有泥"""


def test_what_the_telling_does_read_by_rule_off_the_record():
    assert control.failed(RECORD) and control.settled(RECORD) == ["dice：侦查 45 → 93 失败 ✗"]
    assert not control.failed(RECORD.replace("- dice：侦查 45 → 93 失败 ✗", "- dice：侦查 45 → 93 失败 ✗\n- dice：花费幸运 48 点，侦查 检定改为成功"))
    said = "我先假装去告诉阿萨塔蹲守屋子，在确保他不会听到的地方让他偷偷跟踪我们"
    assert control.secret_spoken(said, "你朝门廊那边扬声交代了一句。他点点头。") == ["你朝门廊那边扬声交代了一句。"]
    assert control.secret_spoken("我去书房", "你朝门廊那边喊了一声。") == []  # nothing meant quietly
    assert ["划破" in x for x in control.unhurt("你的手被铁丝划破了，渗出血来。", RECORD)] == [True]
    assert control.unhurt("你的手被铁丝划破了。", RECORD.replace("- dice", "- damage：受伤 2\n- dice")) == []
    got = control.presence_rules(RECORD, "你喊了一声，里面没有应答。客厅里没有人在。阿萨塔从门口探进头来。阿萨塔不在这里。")
    assert got["nobody"] == ["你喊了一声，里面没有应答。"]  # 托马斯 is in the study, a room of this house
    assert got["absent"] == ["阿萨塔（在主街午餐柜台）：阿萨塔从门口探进头来。"]
    m = Module.load(EXAMPLE)
    gears = next(c.text for c in m.clues if c.id == "c_gears")
    ruled = control.ruled(m, RECORD, said, f"你凑近看：{gears}", [], found=[], public="", ruling={"clues_on_success": ["c_gears"]})
    assert ruled["clues"] == {"missed_told": ["c_gears"], "early_told": ["c_gears"]} and ruled["rules"]["nobody"] == []


def _report(rows: list[tuple[int, int, int]]) -> dict:
    """turn, judged problems, NPC re-asks."""
    return {"settings": {"prompts": {}}, "rows": [
        {"turn": t, "replayed": {"problems": [{"kind": "在场", "claim": "", "record": ""}] * p, "keeper": "雾很大。", "chars": 4,
                                 "control": {"scene": {"first": "held", "second": "", "settled": 0, "reasons": []},
                                             "npc": {"reasks": r, "unsettled": 0}, "fields": {}, "arbiter": {}}}}
        for t, p, r in rows]}  # fmt: skip


def test_two_variants_set_against_each_other_turn_by_turn():
    a = [_report([(1, 2, 1), (2, 1, 0), (3, 1, 0), (4, 0, 0)]), _report([(1, 2, 1), (2, 1, 0), (3, 2, 0), (4, 0, 0)])]
    b = [_report([(1, 0, 0), (2, 0, 0), (3, 1, 0), (4, 0, 0)]), _report([(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 1)])]
    got = session.compare(a, b)
    assert got["turns"] == [1, 2, 3, 4] and got["runs"] == [2, 2]
    p = got["metrics"]["problems"]
    assert p["a"] == [4.0, 0.0] or p["a"][0] == 4.5
    assert (p["b_better"], p["b_worse"]) == (3, 0) and p["p"] == 0.25  # three of three turns: p = 2 / 8
    assert got["metrics"]["problems:在场"]["b_better"] == 3
    assert got["metrics"]["npc_reasks"]["b_better"] == 1 and got["metrics"]["npc_reasks"]["b_worse"] == 1
    assert set(got["style"]["a"]) == {"excess", "similes_per_1000", "sentence_cv"}


def test_a_run_summed_up_with_the_engines_checks():
    controls = [{"scene": {"first": "held", "second": "", "settled": 0, "reasons": []},
                 "npc": {"replies": 1, "reasks": 0, "unsettled": 0, "reasons": []},
                 "fields": {"said_missing": [], "scene_vs_reply": [], "meetings_offered": 0, "meetings_marked": 0},
                 "arbiter": {"invalid_clues": {}, "found_again": []}},
                {"scene": {"first": "sent_back", "second": "sent_back", "settled": 1, "reasons": ["来人赶不到", "来人赶不到"]},
                 "npc": {"replies": 2, "reasks": 2, "unsettled": 1, "reasons": ["同行"]},
                 "fields": {"said_missing": ["thomas:accompany"], "scene_vs_reply": [], "meetings_offered": 1, "meetings_marked": 1},
                 "arbiter": {"invalid_clues": {"c_x": "不存在"}, "found_again": []}}]
    got = session.control_summary(controls, [{"agent": "arbiter", "repairs": 1}, {"agent": "keeper", "repairs": 2}])
    assert got["scene"] == {"told": 2, "held": 1, "sent_back": 1, "kept_partial": 0, "no_scene": 0, "second_failed": 1,
                            "reasons": {"来人赶不到": 2}}
    assert got["npc_checks"] == {"replies": 3, "reasks": 2, "unsettled": 1, "reasons": {"同行": 1}}
    assert got["fields"]["said_missing"] == 1 and got["arbiter"] == {"repairs": 1, "invalid_clues": 1, "found_again": 0}
