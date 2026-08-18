import pytest

import server
from models.spatial import SpatialState


def test_spatial_context_uses_server_npc_presence(monkeypatch) -> None:
    monkeypatch.setattr(server, "get_agent_names", lambda include_narrator=False: ["linxi", "shenzhiyi"])
    monkeypatch.setattr(
        server,
        "read_spatial_state",
        lambda: SpatialState(npc_locations={"linxi": "clubroom", "shenzhiyi": "rooftop"}),
    )

    prompt, audience, target = server._prepare_spatial_context(
        server.SpatialChatContext(
            map_id="clubroom",
            primary_target="linxi",
            visible_to=["linxi", "shenzhiyi"],
        )
    )

    assert target == "linxi"
    assert audience == ["linxi"]
    assert "剧社活动室" in prompt


def test_spatial_context_rejects_target_on_another_map(monkeypatch) -> None:
    monkeypatch.setattr(server, "get_agent_names", lambda include_narrator=False: ["linxi"])
    monkeypatch.setattr(
        server,
        "read_spatial_state",
        lambda: SpatialState(npc_locations={"linxi": "rooftop"}),
    )

    with pytest.raises(server.HTTPException) as exc:
        server._prepare_spatial_context(
            server.SpatialChatContext(map_id="clubroom", primary_target="linxi")
        )
    assert exc.value.status_code == 409
