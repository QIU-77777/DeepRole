from app.conversation_service import ConversationService
from app.llm_schema import LLMToolCall
from models.spatial import SpatialState
import repository.spatial_state as spatial_state


def test_character_tool_call_can_only_move_itself(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(spatial_state, "STATE_PATH", tmp_path / "spatial_state.json")
    monkeypatch.setattr(spatial_state, "CHARACTERS_DIR", tmp_path)
    spatial_state.write_spatial_state(SpatialState(npc_locations={"linxi": "clubroom"}))

    service = ConversationService()
    service._apply_tool_calls(
        "linxi",
        [LLMToolCall(name="move_npc", npc_id="linxi", destination="rooftop")],
    )
    assert spatial_state.read_spatial_state().npc_locations["linxi"] == "rooftop"

    service._apply_tool_calls(
        "linxi",
        [LLMToolCall(name="move_npc", npc_id="shenzhiyi", destination="campus_center")],
    )
    assert spatial_state.read_spatial_state().npc_locations.get("shenzhiyi") is None


def test_character_can_only_start_own_following_when_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(spatial_state, "STATE_PATH", tmp_path / "spatial_state.json")
    monkeypatch.setattr(spatial_state, "CHARACTERS_DIR", tmp_path)
    spatial_state.write_spatial_state(SpatialState(
        player={"map_id": "clubroom", "x": 400, "y": 400, "spawn_id": "clubroom_door"},
        npc_locations={"linxi": "clubroom", "shenzhiyi": "rooftop"},
        npc_overrides={"shenzhiyi": "rooftop"},
    ))

    service = ConversationService()
    service._apply_tool_calls(
        "linxi",
        [LLMToolCall(name="set_following", npc_id="linxi", following=True)],
    )
    assert spatial_state.read_spatial_state().active_followers == ["linxi"]

    result = service._apply_tool_calls(
        "shenzhiyi",
        [LLMToolCall(name="set_following", npc_id="shenzhiyi", following=True)],
    )
    assert result == [{"name": "set_following", "ok": False, "reason": "not_present"}]
