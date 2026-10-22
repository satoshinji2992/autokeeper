"""NPC prompt context follows the scenario while retaining disclosure requirements."""

from autokeeper.agents.cast import Cast
from autokeeper.config import Config
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE


def test_npc_voice_uses_scenario_context_without_losing_clue_gates():
    module = Module.load(EXAMPLE)
    module.era = "1895 年"
    module.setting = "法国，一座港口城市"
    npc = next(person for person in module.npcs if person.gates)
    npc.voice = "公务场合使用正式用语，与熟人交谈时较随和"
    prompt = Cast(Config(), module)._npc_system(npc)

    assert f"年代：{module.era}" in prompt and f"地点：{module.setting}" in prompt
    assert "1920 年代" not in prompt
    assert npc.voice in prompt and all(secret in prompt for secret in npc.secrets)
    for clue_id, gate in npc.gates.items():
        assert module.clue(clue_id).text.strip() in prompt
        assert f"门槛：{gate.skill}·{gate.difficulty}" in prompt


def test_a_habit_in_the_voice_is_shown_now_and_then_not_on_every_line():
    """2026-10-22: a voice of 「说话时总先发出一阵咳嗽」 opened every line with a long cough."""
    module = Module.load(EXAMPLE)
    npc = module.npc("warden")
    npc.voice = "少言，说话时总先发出一阵软弱却刺耳的咳嗽"
    prompt = Cast(Config(), module)._npc_system(npc)
    assert npc.voice in prompt
    assert "口头禅、咳嗽、小动作是这个人的习惯，隔几句露一次就够，资料写着“总是”也不必句句都带" in prompt
    assert "不自己加重伤病和经历的程度" in prompt  # a prepared excuse is said as written, not made worse
