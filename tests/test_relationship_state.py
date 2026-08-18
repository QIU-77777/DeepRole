from models.relationship import relationship_entry_from_description
import repository.relationship_state as relationship_state


def test_relationship_description_derives_explainable_stage_and_tags() -> None:
    entry = relationship_entry_from_description("和玩家是暧昧中的朋友，彼此信任，也有好感")

    assert entry.stage == "暧昧"
    assert entry.tags == ["信任", "好感"]
    assert entry.description.startswith("和玩家是暧昧")


def test_relationship_state_round_trip_and_sync(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(relationship_state, "STATE_PATH", tmp_path / "relationship_state.json")
    monkeypatch.setattr(relationship_state, "CHARACTERS_DIR", tmp_path)

    relationship_state.sync_relationship_from_status(
        "linxi", {"和玩家的关系": "朋友，逐渐信任"}
    )
    restored = relationship_state.read_relationship_state()

    assert restored.characters["linxi"].stage == "朋友"
    assert restored.characters["linxi"].tags == ["信任"]
