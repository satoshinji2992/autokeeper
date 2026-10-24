"""Transport schemas stay complete while dropping labels unused by validation."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from autokeeper.agents.cast import Dialogue, HostedReply, KeeperToolCall, Plan, Ruling, Telling
from autokeeper.agents.director import Improv
from autokeeper.harness import models as hm


def test_compaction_preserves_named_fields_literals_and_constraints():
    schema = {
        "title": "Story", "type": "object",
        "properties": {
            "title": {"title": "Title", "type": "string", "minLength": 2, "description": "玩家所见标题"},
            "payload": {"title": "Payload", "const": {"title": "必须原样保留"},
                        "default": {"title": "默认标题"}, "examples": [{"title": "示例标题"}]},
            "choice": {"title": "Choice", "anyOf": [{"$ref": "#/$defs/title"}, {"type": "null"}]},
        },
        "$defs": {"title": {"title": "NamedDefinition", "type": "integer", "minimum": 1}},
        "required": ["title", "payload"], "additionalProperties": False,
    }
    before = deepcopy(schema)
    compact = hm.compact_schema(schema)
    assert schema == before and compact["title"] == "Story"
    assert compact["properties"]["title"] == {"type": "string", "minLength": 2, "description": "玩家所见标题"}
    assert compact["properties"]["payload"] == {
        "const": {"title": "必须原样保留"}, "default": {"title": "默认标题"}, "examples": [{"title": "示例标题"}],
    }
    assert compact["$defs"]["title"] == {"type": "integer", "minimum": 1}
    for data, valid in [
        ({"title": "新标题", "payload": {"title": "必须原样保留"}, "choice": 1}, True),
        ({"title": "短", "payload": {"title": "必须原样保留"}}, False),
        ({"title": "新标题", "payload": {"title": "被改过"}}, False),
        ({"title": "新标题", "payload": {"title": "必须原样保留"}, "choice": 0}, False),
        ({"title": "新标题", "payload": {"title": "必须原样保留"}, "extra": 1}, False),
    ]:
        assert Draft202012Validator(schema).is_valid(data) is valid
        assert Draft202012Validator(compact).is_valid(data) is valid


def test_schema_combinators_are_walked_but_literal_dictionaries_are_not():
    schema = {
        "allOf": [{"title": "Object", "properties": {"title": {"title": "Field", "type": "string"}}}],
        "items": {"title": "Entry", "type": "string"},
        "prefixItems": [{"title": "First", "type": "integer"}],
        "additionalProperties": {"title": "Value", "type": "boolean"},
        "if": {"title": "Condition", "required": ["title"]},
        "then": {"title": "Consequence", "minProperties": 2},
        "dependentSchemas": {"title": {"title": "Dependent", "required": ["other"]}},
        "enum": [{"title": "literal"}],
    }
    compact = hm.compact_schema(schema)
    assert compact["allOf"] == [{"properties": {"title": {"type": "string"}}}]
    assert compact["items"] == {"type": "string"}
    assert compact["prefixItems"] == [{"type": "integer"}]
    assert compact["additionalProperties"] == {"type": "boolean"}
    assert compact["if"] == {"required": ["title"]}
    assert compact["then"] == {"minProperties": 2}
    assert compact["dependentSchemas"] == {"title": {"required": ["other"]}}
    assert compact["enum"] == [{"title": "literal"}]


@pytest.mark.parametrize("model", [Plan, Ruling, Dialogue, Telling, Improv, HostedReply, KeeperToolCall])
def test_game_contracts_keep_all_fields_descriptions_and_examples(model):
    schema = model.model_json_schema()
    compact = hm.compact_schema(schema)
    Draft202012Validator.check_schema(compact)

    def annotations(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    yield value
                else:
                    yield from annotations(value)
        elif isinstance(node, list):
            for value in node:
                yield from annotations(value)

    assert list(annotations(compact)) == list(annotations(schema))
    assert hm.example(compact) == hm.example(schema)
    assert compact["properties"].keys() == schema["properties"].keys()
    assert len(hm.schema_text(schema)) < len(json.dumps(schema, ensure_ascii=False))
    if model is Dialogue:
        assert "accompany" in compact["properties"] and "accompany" in hm.example(compact)
    if model is Telling:
        assert "asides" in compact["properties"]
