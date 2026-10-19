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
