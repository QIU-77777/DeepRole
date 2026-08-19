import sqlite3

import pytest

from models import Understanding
import repository.vector_store as vector_store_module
from repository.vector_store import VectorStore


TEST_EMBED_DIM = 8


@pytest.fixture
def fake_embedding(monkeypatch):
    calls: list[list[str]] = []

    async def _fake_embed_async(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.0] * TEST_EMBED_DIM for _ in texts]

    monkeypatch.setattr(vector_store_module, "embed_async", _fake_embed_async)
    return calls


@pytest.mark.asyncio
async def test_add_understanding_indexes_vector_and_bm25(tmp_path, monkeypatch, fake_embedding):
    db_path = tmp_path / "vectors.sqlite"
    monkeypatch.setattr(vector_store_module, "vector_db_path", lambda: str(db_path))
    store = VectorStore()

    await store.add_understanding(
        Understanding(
            id="u1",
            memory_owner="alice",
            subject="对玩家的认知",
            keywords=["玩家", "保护欲"],
            content="玩家在压力下会先确认她是否安全。",
            linked_episodes=["e1"],
        )
    )
    await store.close()

    assert fake_embedding == [["对玩家的认知\n玩家、保护欲\n玩家在压力下会先确认她是否安全。"]]

    conn = sqlite3.connect(str(db_path))
    try:
        VectorStore._load_sqlite_vec_sync(conn)
        row = conn.execute(
            "SELECT id, memory_owner, subject, content, keywords, linked_episodes "
            "FROM Understanding"
        ).fetchone()
        assert row == (
            "u1",
            "alice",
            "对玩家的认知",
            "玩家在压力下会先确认她是否安全。",
            "玩家、保护欲",
            '["e1"]',
        )

        vector_rows = store.get_understanding_vector_candidates(
            conn, "alice", [0.0] * TEST_EMBED_DIM, 5
        )
        assert [(row[1], row[2]) for row in vector_rows] == [("u1", "对玩家的认知")]

        bm25_rows = store.get_understanding_bm25_candidates(conn, "alice", "保护欲", 5)
        assert [(row[1], row[4]) for row in bm25_rows] == [("u1", "玩家、保护欲")]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_auto_embedding_dimension_uses_first_real_embedding(tmp_path, monkeypatch):
    db_path = tmp_path / "vectors.sqlite"
    monkeypatch.setattr(vector_store_module, "vector_db_path", lambda: str(db_path))
    calls: list[list[str]] = []

    async def _fake_embed_async(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.0] * TEST_EMBED_DIM for _ in texts]

    monkeypatch.setattr(vector_store_module, "embed_async", _fake_embed_async)
    store = VectorStore()
    await store.add_understanding(
        Understanding(
            id="u1",
            memory_owner="alice",
            subject="对玩家的认知",
            content="玩家会解释误会。",
        )
    )
    await store.close()

    assert calls == [["对玩家的认知\n玩家会解释误会。"]]

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE name IN ('EpisodeMemory_vec', 'Understanding_vec') "
            "ORDER BY name"
        ).fetchall()
        assert rows == [
            (
                "EpisodeMemory_vec",
                "CREATE VIRTUAL TABLE EpisodeMemory_vec USING vec0(\n"
                f"                    embedding F32[{TEST_EMBED_DIM}]\n"
                "                )",
            ),
            (
                "Understanding_vec",
                "CREATE VIRTUAL TABLE Understanding_vec USING vec0(\n"
                f"                    embedding F32[{TEST_EMBED_DIM}]\n"
                "                )",
            ),
        ]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_delete_understanding_owner_removes_understanding_tables(
    tmp_path, monkeypatch, fake_embedding
):
    db_path = tmp_path / "vectors.sqlite"
    monkeypatch.setattr(vector_store_module, "vector_db_path", lambda: str(db_path))
    store = VectorStore()

    await store.add_understanding(
        Understanding(
            id="alice-u1",
            memory_owner="alice",
            subject="对玩家的认知",
            content="玩家会解释误会。",
        )
    )
    await store.add_understanding(
        Understanding(
            id="bob-u1",
            memory_owner="bob",
            subject="对玩家的认知",
            content="玩家会保持距离。",
        )
    )

    assert await store.delete("alice") is True

    db = await store._get_db()
    rows = await db.execute_fetchall(
        "SELECT id, memory_owner FROM Understanding ORDER BY id"
    )
    vec_count = (
        await (await db.execute("SELECT COUNT(*) FROM Understanding_vec")).fetchone()
    )[0]
    fts_count = (
        await (await db.execute("SELECT COUNT(*) FROM Understanding_fts")).fetchone()
    )[0]
    await store.close()

    assert rows == [("bob-u1", "bob")]
    assert vec_count == 1
    assert fts_count == 1


@pytest.mark.asyncio
async def test_delete_all_agents_full_clear_removes_understandings(
    tmp_path, monkeypatch, fake_embedding
):
    db_path = tmp_path / "vectors.sqlite"
    monkeypatch.setattr(vector_store_module, "vector_db_path", lambda: str(db_path))
    monkeypatch.setattr(vector_store_module, "get_agent_names", lambda: ["alice", "bob"])
    store = VectorStore()

    await store.add_understanding(
        Understanding(id="alice-u1", memory_owner="alice", content="Alice 理解。")
    )
    await store.add_understanding(
        Understanding(id="bob-u1", memory_owner="bob", content="Bob 理解。")
    )

    result = await store.delete_all_agents(["alice", "bob"])

    db = await store._get_db()
    understanding_count = (
        await (await db.execute("SELECT COUNT(*) FROM Understanding")).fetchone()
    )[0]
    vec_count = (
        await (await db.execute("SELECT COUNT(*) FROM Understanding_vec")).fetchone()
    )[0]
    fts_count = (
        await (await db.execute("SELECT COUNT(*) FROM Understanding_fts")).fetchone()
    )[0]
    await store.close()

    assert result == {"alice": True, "bob": True}
    assert understanding_count == 0
    assert vec_count == 0
    assert fts_count == 0
