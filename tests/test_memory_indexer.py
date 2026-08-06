from pathlib import Path

import pytest

import app.memory.indexer as indexer
import repository.memory_store as memory_store_module
import repository.vector_store as vector_store_module
from models import EpisodeMemory, Understanding
from repository.memory_store import serialize_episode, write_understandings
from repository.vector_store import VectorStore


class FakeVectorStore:
    def __init__(self) -> None:
        self.deleted_agents: list[str] = []
        self.deleted_all: list[list[str]] = []
        self.added_records: list[EpisodeMemory] = []
        self.added_understandings: list[Understanding] = []
        self.batch_sizes: list[int] = []

    async def delete(self, agent_name: str) -> bool:
        self.deleted_agents.append(agent_name)
        return True

    async def delete_all_agents(self, agent_names: list[str]) -> dict[str, bool]:
        self.deleted_all.append(list(agent_names))
        return {name: True for name in agent_names}

    async def add_episodes(self, records: list[EpisodeMemory], *, batch_size: int = 64) -> int:
        self.added_records.extend(records)
        self.batch_sizes.append(batch_size)
        return len(records)

    async def add_understandings(self, understandings: list[Understanding], *, batch_size: int = 64) -> int:
        self.added_understandings.extend(understandings)
        return len(understandings)


def _write_memory_jsonl(tmp_path: Path, agent_name: str, records: list[EpisodeMemory]) -> None:
    agent_dir = tmp_path / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    with (agent_dir / "memory.jsonl").open("w", encoding="utf-8") as f:
        for record in records:
            f.write(serialize_episode(record) + "\n")


def _patch_character_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _character_path(agent_name: str, *subpaths: str) -> str:
        return str(tmp_path / agent_name / Path(*subpaths))

    monkeypatch.setattr(memory_store_module, "character_path", _character_path)


def _write_single_understanding(agent: str, uid: str = "u1") -> None:
    write_understandings(
        agent,
        {
            uid: Understanding(
                id=uid,
                memory_owner=agent,
                subject="对玩家的认知",
                content="玩家会解释误会。",
            )
        },
    )


@pytest.mark.asyncio
async def test_rebuild_memory_index_uses_batch_add_many(tmp_path, monkeypatch):
    records = [
        EpisodeMemory(date="4月3日", content="第一条批量重建记忆。", memory_owner="alice"),
        EpisodeMemory(date="4月3日", content="第二条批量重建记忆。", memory_owner="alice"),
    ]
    _write_memory_jsonl(tmp_path, "alice", records)
    _patch_character_path(tmp_path, monkeypatch)
    _write_single_understanding("alice")

    fake_store = FakeVectorStore()
    monkeypatch.setattr(indexer, "vector_store", fake_store)
    monkeypatch.setattr(indexer, "get_agent_names", lambda include_narrator=False: ["alice"])

    await indexer.rebuild_memory_index(batch_size=2)

    assert fake_store.deleted_all == [["alice"]]
    assert fake_store.deleted_agents == []
    assert fake_store.batch_sizes == [2]
    assert [record.content for record in fake_store.added_records] == [
        "第一条批量重建记忆。",
        "第二条批量重建记忆。",
    ]
    assert [u.id for u in fake_store.added_understandings] == ["u1"]


@pytest.mark.asyncio
async def test_rebuild_memory_index_can_skip_clear_existing(tmp_path, monkeypatch):
    _write_memory_jsonl(
        tmp_path,
        "alice",
        [EpisodeMemory(date="4月3日", content="无需重复清理的重建记忆。", memory_owner="alice")],
    )
    _patch_character_path(tmp_path, monkeypatch)

    fake_store = FakeVectorStore()
    monkeypatch.setattr(indexer, "vector_store", fake_store)
    monkeypatch.setattr(indexer, "get_agent_names", lambda include_narrator=False: ["alice"])

    await indexer.rebuild_memory_index(clear_existing=False)

    assert fake_store.deleted_all == []
    assert fake_store.deleted_agents == []
    assert len(fake_store.added_records) == 1
    assert fake_store.added_understandings == []


@pytest.mark.asyncio
async def test_rebuild_memory_index_restores_understandings_for_single_agent(tmp_path, monkeypatch):
    _write_memory_jsonl(
        tmp_path,
        "alice",
        [EpisodeMemory(date="4月3日", content="完整重建情节记忆。", memory_owner="alice")],
    )
    _patch_character_path(tmp_path, monkeypatch)
    _write_single_understanding("alice")

    fake_store = FakeVectorStore()
    monkeypatch.setattr(indexer, "vector_store", fake_store)

    await indexer.rebuild_memory_index(agent_name="alice")

    assert fake_store.deleted_agents == ["alice"]
    assert fake_store.deleted_all == []
    assert len(fake_store.added_records) == 1
    assert [u.id for u in fake_store.added_understandings] == ["u1"]


@pytest.mark.asyncio
async def test_vector_store_add_many_batches_embedding_requests(tmp_path, monkeypatch):
    store = VectorStore()
    monkeypatch.setattr(store, "character_path", lambda name, *parts: str(tmp_path / name / Path(*parts)))

    embedding_calls: list[list[str]] = []
    inserted_batches: list[int] = []

    async def fake_embed_async(texts: list[str]) -> list[list[float]]:
        embedding_calls.append(texts)
        return [[0.0] for _ in texts]

    async def fake_insert_prepared_batch(prepared, embeddings) -> int:
        inserted_batches.append(len(prepared))
        assert len(prepared) == len(embeddings)
        return len(prepared)

    monkeypatch.setattr(vector_store_module, "embed_async", fake_embed_async)
    monkeypatch.setattr(store, "_insert_prepared_batch", fake_insert_prepared_batch)

    inserted = await store.add_episodes(
        [
            EpisodeMemory(date="4月3日", content="第一条。", memory_owner="alice", title="标题一"),
            EpisodeMemory(date="4月3日", content="第二条。", memory_owner="alice", title="标题二"),
            EpisodeMemory(date="4月4日", content="第三条。", memory_owner="alice", title="标题三"),
        ],
        batch_size=2,
    )

    assert inserted == 3
    assert [len(call) for call in embedding_calls] == [2, 1]
    assert inserted_batches == [2, 1]


@pytest.mark.asyncio
async def test_rebuild_understanding_index_adds_each_understanding(tmp_path, monkeypatch):
    _patch_character_path(tmp_path, monkeypatch)
    write_understandings(
        "alice",
        {
            "u1": Understanding(
                id="u1",
                memory_owner="alice",
                subject="对玩家的认知",
                content="玩家在紧张时会先确认她是否安全。",
            ),
            "u2": Understanding(
                id="u2",
                memory_owner="alice",
                subject="互动模式",
                content="直接提问更容易得到真实回应。",
            ),
        },
    )

    fake_store = FakeVectorStore()
    monkeypatch.setattr(indexer, "vector_store", fake_store)
    monkeypatch.setattr(indexer, "get_agent_names", lambda include_narrator=False: ["alice"])

    await indexer.rebuild_understanding_index()

    assert [u.id for u in fake_store.added_understandings] == ["u1", "u2"]
    assert [u.memory_owner for u in fake_store.added_understandings] == ["alice", "alice"]
