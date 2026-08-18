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
