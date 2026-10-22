"""JSON examples describe idle defaults without inventing actions or hiding the full contract."""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper.agents.cast import Action, Dialogue, Plan, Ruling, Telling
from autokeeper.agents.director import Improv
from autokeeper.harness import models


@pytest.mark.parametrize("contract", [Action, Dialogue, Plan, Ruling, Telling, Improv])
def test_an_example_keeps_every_field_and_passes_its_full_schema(contract):
    schema = contract.model_json_schema()
    before = copy.deepcopy(schema)
    sample = models.example(schema)
    assert set(sample) == set(schema["properties"])
    assert not models.problems(sample, schema)
    assert schema == before
    assert models.schema_text(schema) in models.json_instruction(schema)


def test_ordinary_dialogue_and_narration_do_not_demonstrate_unrequested_operations():
    dialogue = models.example(Dialogue.model_json_schema())
    assert dialogue["intentions"] is None and dialogue["media"] == []
    assert dialogue["accompany"] == "" and dialogue["changes"] == [] and dialogue["items"] == []
    narration = models.example(Telling.model_json_schema())
    assert narration["asides"] == narration["media"] == narration["perceptions"] == []
    improv = models.example(Improv.model_json_schema())
    assert improv["backstage"] == improv["plugins"] == improv["media"] == []
    # Empty optional fields in the example do not remove real, explicitly chosen operations.
    response = {"text": "我带您过去，路上再谈。", "accompany": "office",
                "intentions": {"reason": "愿意帮忙找人", "goals": ["带客人到报社"],
                               "steps": [{"id": "visit_paper", "text": "去报社找主编"}]},
                "media": [{"ref": "handout:letter", "to": ["reader"]}]}
    assert not models.problems(response, Dialogue.model_json_schema())
    parsed = Dialogue.model_validate(response)
    assert parsed.accompany == "office" and parsed.intentions.goals == ["带客人到报社"]
    assert parsed.media[0].ref == "handout:letter"


def test_required_array_members_and_default_payloads_keep_their_declared_shape():
    schema = {"type": "object", "properties": {
        "rows": {"type": "array", "minItems": 2, "items": {"$ref": "#/$defs/Row"}},
        "settings": {"default": {"labels": ["默认"]}, "type": "object"},
        "maybe": {"default": None, "anyOf": [{"$ref": "#/$defs/Row"}, {"type": "null"}]},
    }, "required": ["rows"], "$defs": {"Row": {"type": "object", "properties": {
        "kind": {"const": "row"}, "text": {"type": "string"}}, "required": ["kind", "text"]}}}
    sample = models.example(schema)
    assert len(sample["rows"]) == 2 and sample["rows"][0] == {"kind": "row", "text": "…"}
    assert sample["maybe"] is None and not models.problems(sample, schema)
    sample["settings"]["labels"].append("changed")
    assert schema["properties"]["settings"]["default"] == {"labels": ["默认"]}
    assert json.loads(json.dumps(models.example(schema))) == models.example(schema)
