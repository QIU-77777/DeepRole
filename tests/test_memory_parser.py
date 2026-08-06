from pathlib import Path

import repository.memory_store as memory_store_module
from models import EpisodeMemory, Understanding, UnderstandingHistoryEntry
from repository.memory_store import (
    append_memory_records,
    read_memory_jsonl,
    read_understandings,
    understanding_jsonl_path,
    write_understandings,
)


def test_append_memory_records_assigns_episode_ids(tmp_path, monkeypatch):
    def _character_path(agent_name: str, *subpaths: str) -> str:
        return str(tmp_path / agent_name / Path(*subpaths))

    monkeypatch.setattr(memory_store_module, "character_path", _character_path)

    written = append_memory_records(
        "alice",
        [
            EpisodeMemory(content="需要生成 id 的记忆。"),
            EpisodeMemory(id="stable-id", content="已有 id 的记忆。"),
            EpisodeMemory(content="   "),
        ],
    )

    assert len(written) == 2
    assert written[0].id
    assert written[0].id != "stable-id"
    assert written[0].memory_owner == "alice"
    assert written[1].id == "stable-id"
    assert written[1].memory_owner == "alice"

    persisted = read_memory_jsonl("alice")
    assert [record.id for record in persisted] == [written[0].id, "stable-id"]


def test_episode_memory_defaults_last_recalled_at_to_event_date():
    record = EpisodeMemory(date="4月3日 09:00", content="默认召回日期。")

    assert record.last_recalled_at == "4月3日"
    assert "last_recalled_at" not in record.model_fields_set


def test_understanding_jsonl_round_trips_and_skips_bad_lines(tmp_path, monkeypatch):
    def _character_path(agent_name: str, *subpaths: str) -> str:
        return str(tmp_path / agent_name / Path(*subpaths))

    monkeypatch.setattr(memory_store_module, "character_path", _character_path)

    understandings = {
        "u1": Understanding(
            id="u1",
            memory_owner="alice",
            subject="对玩家的认知",
            keywords=["玩家", "信任"],
            content="玩家在压力下会先确认她是否安全。",
            linked_episodes=["e1"],
            history=[
                UnderstandingHistoryEntry(
                    episode_id="e1",
                    date="4月3日",
                    title="旧阅览室",
                    content="玩家在压力下会先确认她是否安全。",
                )
            ],
        ),
        "u2": Understanding(
            id="u2",
            memory_owner="alice",
            subject="互动模式",
            content="直接提问时更容易得到真实反应。",
        ),
    }

    write_understandings("alice", understandings)
    path = understanding_jsonl_path("alice")
    with path.open("a", encoding="utf-8") as f:
        f.write("{bad json}\n")

    loaded = read_understandings("alice")

    assert list(loaded) == ["u1", "u2"]
    assert loaded["u1"].keywords == ["玩家", "信任"]
    assert loaded["u1"].linked_episodes == ["e1"]
    assert loaded["u1"].history[0].episode_id == "e1"
    assert loaded["u1"].history[0].title == "旧阅览室"
    assert loaded["u2"].content == "直接提问时更容易得到真实反应。"
    assert loaded["u2"].history == []


def test_write_understandings_removes_file_when_empty(tmp_path, monkeypatch):
    def _character_path(agent_name: str, *subpaths: str) -> str:
        return str(tmp_path / agent_name / Path(*subpaths))

    monkeypatch.setattr(memory_store_module, "character_path", _character_path)

    write_understandings(
        "alice",
        {"u1": Understanding(id="u1", memory_owner="alice", content="存在过。")},
    )
    path = understanding_jsonl_path("alice")
    assert path.exists()

    write_understandings("alice", {})

    assert not path.exists()
