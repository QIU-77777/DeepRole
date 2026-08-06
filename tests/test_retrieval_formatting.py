import app.memory.retrieval as retrieval_module
from app.memory.retrieval import _format_retrieved_memory, search_memories, search_understandings


def test_format_retrieved_memory_includes_date_heading():
    result = _format_retrieved_memory(
        {
            "date": "4月3日",
            "content": "- **时间**：4月3日 08:00\n- **内容**：收到了录取通知书。",
        }
    )

    assert result.startswith("## 4月3日\n")
    assert "收到了录取通知书" in result


def test_format_retrieved_memory_keeps_undated_content_compatible():
    assert _format_retrieved_memory({"content": "旧格式记忆"}) == "旧格式记忆"


def test_search_memories_returns_empty_when_vector_db_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "missing.sqlite"
    monkeypatch.setattr(retrieval_module, "DB_PATH", str(db_path))

    def fail_candidate_lookup(*_args, **_kwargs):
        raise AssertionError("candidate lookup should not run without a vector DB")

    monkeypatch.setattr(
        retrieval_module.vector_store,
        "get_vector_candidates",
        fail_candidate_lookup,
    )

    assert search_memories("mitsuki", "query", qvec=[0.0]) == "（无相关记忆）"
    assert not db_path.exists()


def test_search_memories_returns_empty_when_vector_schema_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.sqlite"
    db_path.touch()
    monkeypatch.setattr(retrieval_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(retrieval_module.VectorStore, "_load_sqlite_vec_sync", lambda _conn: None)

    def fail_candidate_lookup(*_args, **_kwargs):
        raise AssertionError("candidate lookup should not run without vector tables")

    monkeypatch.setattr(
        retrieval_module.vector_store,
        "get_vector_candidates",
        fail_candidate_lookup,
    )

    assert search_memories("mitsuki", "query", qvec=[0.0]) == "（无相关记忆）"


def test_search_understandings_returns_empty_when_vector_schema_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.sqlite"
    db_path.touch()
    monkeypatch.setattr(retrieval_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(retrieval_module.VectorStore, "_load_sqlite_vec_sync", lambda _conn: None)

    def fail_candidate_lookup(*_args, **_kwargs):
        raise AssertionError("candidate lookup should not run without vector tables")

    monkeypatch.setattr(
        retrieval_module.vector_store,
        "get_understanding_vector_candidates",
        fail_candidate_lookup,
    )

    assert search_understandings("mitsuki", "query", qvec=[0.0]) == ""


def test_search_memories_uses_bm25_query_for_bm25(monkeypatch):
    captured: dict[str, str | None] = {}

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(retrieval_module.sqlite3, "connect", lambda _path: FakeConn())
    monkeypatch.setattr(retrieval_module.VectorStore, "_load_sqlite_vec_sync", lambda _conn: None)
    monkeypatch.setattr(retrieval_module, "_sync_tables_exist", lambda _conn, _names: True)
    monkeypatch.setattr(
        retrieval_module.vector_store,
        "get_vector_candidates",
        lambda _conn, _agent_name, _qvec, _limit: [],
    )

    def fake_bm25(_conn, _agent_name, query, _limit):
        captured["query"] = query
        return []

    monkeypatch.setattr(retrieval_module.vector_store, "get_bm25_candidates", fake_bm25)
    monkeypatch.setattr(retrieval_module, "HYBRID_SEARCH_ENABLED", True)
    monkeypatch.setattr(retrieval_module, "_load_current_game_date", lambda: "4月16日")

    def fake_log(**kwargs):
        captured["embedding_query"] = kwargs["embedding_query"]
        captured["bm25_query"] = kwargs["bm25_query"]

    monkeypatch.setattr(retrieval_module, "log_retrieval_results", fake_log)

    result = search_memories(
        "mitsuki",
        "semantic scene query",
        qvec=[0.0],
        bm25_query="车站 家门口 拒绝",
    )

    assert captured["query"] == "车站 家门口 拒绝"
    assert captured["embedding_query"] == "semantic scene query"
    assert captured["bm25_query"] == "车站 家门口 拒绝"
    assert result == "（无相关记忆）"


def test_search_understandings_logs_semantic_and_bm25_queries(monkeypatch):
    captured: dict[str, str | None] = {}

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(retrieval_module.sqlite3, "connect", lambda _path: FakeConn())
    monkeypatch.setattr(retrieval_module.VectorStore, "_load_sqlite_vec_sync", lambda _conn: None)
    monkeypatch.setattr(retrieval_module, "_sync_tables_exist", lambda _conn, _names: True)
    monkeypatch.setattr(
        retrieval_module.vector_store,
        "get_understanding_vector_candidates",
        lambda _conn, _agent_name, _qvec, _limit: [],
    )

    def fake_bm25(_conn, _agent_name, query, _limit):
        captured["query"] = query
        return []

    monkeypatch.setattr(
        retrieval_module.vector_store,
        "get_understanding_bm25_candidates",
        fake_bm25,
    )
    monkeypatch.setattr(retrieval_module, "HYBRID_SEARCH_ENABLED", True)

    def fake_log(**kwargs):
        captured["embedding_query"] = kwargs["embedding_query"]
        captured["bm25_query"] = kwargs["bm25_query"]

    monkeypatch.setattr(retrieval_module, "log_retrieval_results", fake_log)

    result = search_understandings(
        "mitsuki",
        "long understanding query",
        qvec=[0.0],
        bm25_query="关系 约定 放学",
    )

    assert captured["query"] == "关系 约定 放学"
    assert captured["embedding_query"] == "long understanding query"
    assert captured["bm25_query"] == "关系 约定 放学"
    assert result == ""
