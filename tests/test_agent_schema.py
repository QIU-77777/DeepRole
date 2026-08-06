"""测试 Agent 结构化输出模型。"""

import pytest
from pydantic import ValidationError

from app.llm_schema import (
    MAX_CHOICE_CHARS,
    MAX_EPISODE_KEYWORDS,
    LLMChoices,
    LLMEpisodeMemory,
    LLMNarratorOutput,
    LLMStateUpdate,
    LLMUnderstandingPatch,
)
from conftest import _narrator_output


def test_choices_output_trims_each_choice_to_50_chars():
    long_choice = "我" * (MAX_CHOICE_CHARS + 8)

    output = LLMChoices(choices=[f"  {long_choice}  ", "短选项"])

    assert output.choices[0] == "我" * MAX_CHOICE_CHARS
    assert len(output.choices[0]) == MAX_CHOICE_CHARS
    assert output.choices[1] == "短选项"


def test_narrator_output_requires_route_target_or_new_character():
    with pytest.raises(ValidationError):
        _narrator_output(targets=[], new_characters=[])


def test_narrator_output_allows_new_character_without_existing_target():
    output = _narrator_output(
        targets=[],
        scene_description="门外有人停下脚步。",
        new_characters=[
            {
                "background_hint": "玩家刚认识的邻班学生，安静内敛，总是一个人坐在走廊角落。",
            }
        ],
    )

    assert output.targets == []
    assert output.new_characters[0].background_hint == "玩家刚认识的邻班学生，安静内敛，总是一个人坐在走廊角落。"


def test_episode_memory_block_cleans_keywords_and_clamps_importance():
    block = LLMEpisodeMemory(
        date="10月19日",
        time="10月19日 晚上",
        location="餐厅",
        participants="我、他",
        keywords=[" 餐厅 ", "", "吃饭", "  ", 123, "约定", "关系", "多余", "昵称", "截断"],
        importance=9,
        content="一起吃饭。",
    )

    assert block.keywords == ["餐厅", "吃饭", "123", "约定", "关系", "多余", "昵称", "截断"]
    assert len(block.keywords) == MAX_EPISODE_KEYWORDS
    assert block.importance == 5


def test_episode_memory_block_defaults_bad_metadata():
    block = LLMEpisodeMemory(
        date="10月19日",
        time="10月19日 晚上",
        location="餐厅",
        participants="我、他",
        keywords="餐厅,吃饭",
        importance="bad",
        content="一起吃饭。",
    )

    assert block.keywords == []
    assert block.importance == 3


def test_understanding_patch_output_defaults_to_empty_operations():
    output = LLMUnderstandingPatch()

    assert output.add == []
    assert output.update == {}
    assert not hasattr(output, "remove")


def test_understanding_entry_strips_string_fields():
    output = LLMUnderstandingPatch.model_validate(
        {
            "add": [
                {
                    "subject": "  对玩家的认知  ",
                    "keywords": ["玩家"],
                    "content": "  玩家会主动解释误会。  ",
                }
            ]
        }
    )

    entry = output.add[0]
    assert entry.subject == "对玩家的认知"
    assert entry.content == "玩家会主动解释误会。"


def test_state_updater_output_accepts_world_schedule_fields():
    default_output = LLMStateUpdate()
    assert default_output.recent_world_event == ""
    assert default_output.world_schedule_update == ""
    assert default_output.triggered_world_events == []

    output = LLMStateUpdate(
        recent_world_event="（准备期）文化祭准备中",
        world_schedule_update='{"events":[]}',
        triggered_world_events=["文化祭主题讨论"],
    )
    assert output.recent_world_event == "（准备期）文化祭准备中"
    assert output.world_schedule_update == '{"events":[]}'
    assert output.triggered_world_events == ["文化祭主题讨论"]


def test_understanding_update_allows_subject_only_entry():
    output = LLMUnderstandingPatch.model_validate(
        {"update": {"u1": {"subject": "对玩家的认知"}}}
    )

    entry = output.update["u1"]
    assert entry.subject == "对玩家的认知"
    assert entry.keywords == []
    assert entry.content == ""
