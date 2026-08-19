"""测试 raw history 的 turn 分配规则。"""

import os
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
os.chdir(project_root)

import repository.message_router as router_module
import repository.runtime_state as runtime_state_module
from repository.runtime_state import PLAYER_NAME_FILENAME
from repository.message_router import MessageRouter


@pytest.fixture(autouse=True)
def clear_player_name_between_tests():
    yield


@pytest.mark.asyncio
async def test_narrator_response_starts_next_turn(tmp_path, monkeypatch):
    current_turn = 5
    written: list[dict] = []

    def fake_read_turn_counter() -> int:
        return current_turn

    def fake_increment_turn_counter() -> int:
        nonlocal current_turn
        current_turn += 1
        return current_turn

    async def fake_append_message(message: dict) -> None:
        written.append(dict(message))

    monkeypatch.setattr(runtime_state_module, "characters_dir", lambda: tmp_path)
    monkeypatch.setattr(router_module, "read_turn_counter", fake_read_turn_counter)
    monkeypatch.setattr(router_module, "increment_turn_counter", fake_increment_turn_counter)
    monkeypatch.setattr(router_module, "append_message", fake_append_message)

    router = MessageRouter()

    player_turn = await router.broadcast_player_message(["mitsuki"], "上一段旁白后的回应")
    narrator_turn = await router.broadcast_agent_response("narrator", ["mitsuki"], "新的场景")
    character_turn = await router.broadcast_agent_response("mitsuki", ["mitsuki"], "角色回应")

    assert player_turn == 5
    assert narrator_turn == 6
    assert character_turn == 6
    assert [(m["role"], m["turn"]) for m in written] == [
        ("player", 5),
        ("narrator", 6),
        ("mitsuki", 6),
    ]
    assert written[0]["visible_to"] == ["mitsuki", "narrator"]


@pytest.mark.asyncio
async def test_broadcast_narrator_output_writes_structured_scene(tmp_path, monkeypatch):
    current_turn = 2
    written: list[dict] = []

    def fake_increment_turn_counter() -> int:
        nonlocal current_turn
        current_turn += 1
        return current_turn

    async def fake_append_message(message: dict) -> None:
        written.append(dict(message))

    monkeypatch.setattr(runtime_state_module, "characters_dir", lambda: tmp_path)
    monkeypatch.setattr(router_module, "increment_turn_counter", fake_increment_turn_counter)
    monkeypatch.setattr(router_module, "append_message", fake_append_message)

    router = MessageRouter()
    turn = await router.broadcast_narrator_output(
        ["mitsuki"],
        {
            "targets": ["mitsuki"],
            "date": "4月3日 星期三",
            "time": "16:10",
            "location": "走廊",
            "present_characters": {"北原悠": "门口", "美月": "窗边"},
            "scene_description": "走廊里传来广播声。",
            "new_characters": [],
        },
    )

    assert turn == 3
    assert written == [
        {
            "role": "narrator",
            "targets": ["mitsuki"],
            "date": "4月3日 星期三",
            "time": "16:10",
            "location": "走廊",
            "present_characters": {"北原悠": "门口", "美月": "窗边"},
            "scene_description": "走廊里传来广播声。",
            "new_characters": [],
            "visible_to": ["mitsuki", "narrator"],
            "turn": 3,
        }
    ]


@pytest.mark.asyncio
async def test_player_message_extracts_and_reuses_name_for_raw(tmp_path, monkeypatch):
    current_turn = 1
    written: list[dict] = []

    async def fake_append_message(message: dict) -> None:
        written.append(dict(message))

    def fake_read_turn_counter() -> int:
        return current_turn

    monkeypatch.setattr(runtime_state_module, "characters_dir", lambda: tmp_path)
    monkeypatch.setattr(router_module, "read_turn_counter", fake_read_turn_counter)
    monkeypatch.setattr(router_module, "append_message", fake_append_message)

    router = MessageRouter()

    player_turn = await router.broadcast_player_message(["mitsuki"], "我叫北原悠，是个男生")
    assert player_turn == 1
    assert written[0]["content"] == "## 北原悠\n我叫北原悠，是个男生"
    assert (tmp_path / PLAYER_NAME_FILENAME).read_text(encoding="utf-8") == "北原悠"

    current_turn = 6
    player_turn = await router.broadcast_player_message(["mitsuki"], "今天天气好")
    assert player_turn == 6
    assert written[1]["content"] == "## 北原悠\n今天天气好"
