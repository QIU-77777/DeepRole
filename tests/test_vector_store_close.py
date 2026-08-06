import asyncio
from pathlib import Path

import pytest

from repository.vector_store import VectorStore


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.executed: list[str] = []

    async def execute(self, sql: str, *_args: object) -> None:
        await asyncio.sleep(0)
        self.executed.append(sql)

    async def execute_fetchall(self, *_args: object) -> list[tuple]:
        await asyncio.sleep(0)
        return []

    async def commit(self) -> None:
        await asyncio.sleep(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_close_resets_connection_state() -> None:
    store = VectorStore()
    fake_connection = FakeConnection()
    loop = asyncio.get_running_loop()
    store._db = fake_connection  # type: ignore[assignment]
    store._tables_initialized = True
    store._tables_initialized_loop = loop
    store._init_lock = asyncio.Lock()
    store._init_lock_loop = loop
    store._write_lock = asyncio.Lock()
    store._write_lock_loop = loop

    await store.close()

    assert fake_connection.closed is True
    assert store._db is None
    assert store._init_lock is None
    assert store._init_lock_loop is None
    assert store._tables_initialized is False
    assert store._tables_initialized_loop is None
    assert store._write_lock is None
    assert store._write_lock_loop is None


@pytest.mark.asyncio
async def test_close_without_connection_is_noop() -> None:
    store = VectorStore()
    loop = asyncio.get_running_loop()
    store._init_lock = asyncio.Lock()
    store._init_lock_loop = loop

    await store.close()

    assert store._db is None
    assert store._init_lock is None
    assert store._init_lock_loop is None


@pytest.mark.asyncio
async def test_concurrent_export_recall_state_initializes_connection_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import repository.vector_store as vector_store_module

    connections: list[FakeConnection] = []
    load_calls: list[FakeConnection] = []

    async def fake_connect(_path: str) -> FakeConnection:
        await asyncio.sleep(0)
        connection = FakeConnection()
        connections.append(connection)
        return connection

    async def fake_load_sqlite_vec(
        _store: VectorStore,
        connection: FakeConnection,
    ) -> None:
        await asyncio.sleep(0)
        load_calls.append(connection)

    monkeypatch.setattr(
        vector_store_module,
        "DB_PATH",
        str(tmp_path / "vectors.sqlite"),
    )
    monkeypatch.setattr(vector_store_module.aiosqlite, "connect", fake_connect)
    monkeypatch.setattr(VectorStore, "_load_sqlite_vec", fake_load_sqlite_vec)

    store = VectorStore()

    results = await asyncio.gather(
        *(store.export_recall_state(f"agent_{i}") for i in range(4))
    )

    assert results == [{}, {}, {}, {}]
    assert len(connections) == 1
    assert load_calls == connections
    assert store._tables_initialized is False
