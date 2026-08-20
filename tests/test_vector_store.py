"""
向量库 pytest 测试

测试 sqlite-vec 向量库的增/查/删/重建功能
使用真实 embedding API 请求（不 mock）
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# 设置项目根目录，确保相对路径与模块导入一致
project_root = Path(__file__).parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

# 必须在导入 vector_store 之前加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")
except ImportError:
    pass

# 现在导入 vector_store，此时环境变量已加载
try:
    import importlib
    import repository.vector_store  # 确保子模块被加载进 sys.modules
    vector_store_module = importlib.import_module("repository.vector_store")
    from repository.vector_store import vector_store, VectorStore
    from repository.llm.embedding import embed_sync, EMBED_API_URL, EMBED_API_KEY
    import app.memory.retrieval as retrieval_module
    import repository.memory_store as memory_store_module
    from models import EpisodeMemory
    from app.memory.retrieval import (
        hybrid_fusion,
        apply_recency,
        _recency_score,
        _importance_score,
        RERANK_MODEL,
    )
except ModuleNotFoundError as exc:
    pytest.skip(f"skip vector_store tests: missing dependency ({exc})", allow_module_level=True)


# 检查 embedding 配置是否可用
pytestmark = pytest.mark.skipif(
    not EMBED_API_URL or not EMBED_API_KEY,
    reason="EMBEDDING_API_URL 或 EMBEDDING_API_KEY 未配置，跳过测试"
)


# 向量库测试路径由 conftest 的 _isolate_vector_db 提供（每测试独立临时目录，
# 绝不含生产 data/runtime/{user}/vectors.sqlite）；clean_store 在 yield 前把
# DEFAULT 用户对应的路径写入本模块全局，供同步读取辅助函数使用。
test_db_path: str = ""


@pytest_asyncio.fixture
async def clean_store(monkeypatch, _isolate_vector_db):
    """提供清理后的 VectorStore，每个测试隔离。"""
    global test_db_path
    from repository.user_context import current_user_id, DEFAULT_USER_ID
    current_user_id.set(DEFAULT_USER_ID)
    test_db_path = _isolate_vector_db()

    store = vector_store
    await store.close()

    # 确保临时向量库目录存在（同步读取辅助函数可能先于写库打开连接）
    os.makedirs(os.path.dirname(test_db_path), exist_ok=True)

    yield store

    # 清理：关闭数据库连接
    await store.close()


async def wait_for_search(store, agent_name: str, query: str, timeout: float = 10.0):
    """轮询等待向量检索可命中（超时抛出异常）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_error = None

    while asyncio.get_event_loop().time() < deadline:
        try:
            import sqlite3
            conn = sqlite3.connect(test_db_path)
            try:
                VectorStore._load_sqlite_vec_sync(conn)
                qvec = embed_sync([query])[0]
                rows = store.get_vector_candidates(conn, agent_name, qvec, 5)
            finally:
                conn.close()
            if rows:
                return rows
        except Exception as e:
            last_error = e
        await asyncio.sleep(0.1)

    error_msg = f"等待搜索结果超时: agent={agent_name}, query={query}"
    if last_error:
        error_msg += f", last_error={last_error}"
    raise TimeoutError(error_msg)


def make_character_path(tmp_path):
    def _path(name, subpath=None):
        base = tmp_path / name
        if subpath:
            return str(base / subpath)
        return str(base)
    return _path


def _parse_memory_markdown(content: str, memory_owner: str = "") -> list[EpisodeMemory]:
    """把测试里惯用的 memory.md 字符串解析成 EpisodeMemory 列表。

    支持最小格式：
        ## X月X日
        - **时间**：...
        - **地点**：...
        - **在场**：...
        - **关键词**：...（可选，空格或顿号分隔）
        - **重要度**：...（可选，默认 3）
        - **内容**：...

    空行或新的 `- **时间**` 视为分块；非结构化行静默跳过。
    """
    episodes: list[EpisodeMemory] = []
    current_date = ""
    current_fields: dict[str, str] = {}

    def _flush():
        if not current_fields.get("内容"):
            current_fields.clear()
            return
        kw_raw = current_fields.get("关键词", "").strip()
        keywords = [k for k in re.split(r"[、\s]+", kw_raw) if k] if kw_raw else []
        try:
            importance = int(current_fields.get("重要度", "3").strip())
        except ValueError:
            importance = 3
        episodes.append(EpisodeMemory(
            date=current_date,
            time=current_fields.get("时间", "").strip(),
            location=current_fields.get("地点", "").strip(),
            participants=current_fields.get("在场", "").strip(),
            keywords=keywords,
            importance=importance,
            content=current_fields.get("内容", "").strip(),
            memory_owner=memory_owner,
        ))
        current_fields.clear()

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_fields.get("内容"):
                _flush()
            continue
        m_date = re.match(r"##\s*(\d{1,2}月\d{1,2}日)", stripped)
        if m_date:
            _flush()
            current_date = m_date.group(1)
            continue
        if stripped.startswith("#"):
            continue
        m_field = re.match(r"-\s*\*\*(时间|地点|在场|关键词|重要度|内容)\*\*：(.*)", stripped)
        if m_field:
            field, value = m_field.group(1), m_field.group(2).strip()
            if field == "时间" and current_fields.get("内容"):
                _flush()
            current_fields[field] = value
    _flush()
    return episodes


def write_memory(tmp_path, agent_name: str, content: str):
    """把测试里的 memory.md 字符串解析为 EpisodeMemory 后写入 memory.jsonl。"""
    agent_dir = tmp_path / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / "memory.jsonl"
    path.write_text("", encoding="utf-8")  # 清空以保证幂等
    from repository.memory_store import serialize_episode
    episodes = _parse_memory_markdown(content, memory_owner=agent_name)
    with path.open("w", encoding="utf-8") as f:
        for ep in episodes:
            f.write(serialize_episode(ep) + "\n")
    return path


def write_status(tmp_path, agent_name: str, content: str):
    agent_dir = tmp_path / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / "status.md"
    path.write_text(content, encoding="utf-8")
    return path


def _read_episodes(tmp_path, agent_name: str) -> list[EpisodeMemory]:
    """直接从 tmp_path 下的 memory.jsonl 读回 EpisodeMemory 列表。"""
    from repository.memory_store import parse_jsonl_line
    path = tmp_path / agent_name / "memory.jsonl"
    if not path.exists():
        return []
    records: list[EpisodeMemory] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = parse_jsonl_line(line)
        if record is not None:
            records.append(record)
    return records


def get_episodes(tmp_path, agent_name: str, date: str) -> list[EpisodeMemory]:
    """从 tmp_path 下的 memory.jsonl 提取指定日期的 EpisodeMemory 列表。"""
    return [r for r in _read_episodes(tmp_path, agent_name) if r.date == date]


async def add_episodes(store, episodes: list[EpisodeMemory]) -> None:
    for episode in episodes:
        await store.add_episode(episode)


def _chunk_row_by_content(conn, content: str):
    return conn.execute(
        "SELECT content, keywords, importance FROM EpisodeMemory WHERE content = ?",
        (content,),
    ).fetchone()


class TestVectorStoreBasic:
    """基础增查删测试"""

    @pytest.mark.asyncio
    async def test_delete(self, clean_store, tmp_path, monkeypatch):
        """测试删除指定角色的可见记录"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path,
            "lilith",
            (
                "# lilith 的长期记忆\n\n"
                "## 4月3日\n"
                "- **时间**：4月3日 08:00\n"
                "- **地点**：教室\n"
                "- **在场**：莉莉丝\n"
                "- **内容**：早上好，今天天气不错。"
            ),
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))

        res_before = await wait_for_search(store, "lilith", "早上好")
        assert len(res_before) >= 1, "删除前应该有数据"

        ok = await store.delete("lilith")
        assert ok is True, "删除应该成功"

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            qvec = embed_sync(["早上好"])[0]
            res_after = store.get_vector_candidates(conn, "lilith", qvec, 5)
        finally:
            conn.close()
        assert len(res_after) == 0, "删除后应该没有数据"



class TestVectorStoreRebuild:
    """重建功能测试"""

    @pytest.mark.asyncio
    async def test_rebuild_memory_layer(self, clean_store, tmp_path, monkeypatch):
        """测试 rebuild() 从 memory.jsonl + consolidation_state 重建 memory 层向量索引"""
        import importlib; vs_mod = importlib.import_module("repository.vector_store")

        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(memory_store_module, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path,
            "lilith",
            (
                "# lilith 的长期记忆\n\n"
                "## 4月3日\n"
                "- **时间**：4月3日 08:00\n"
                "- **地点**：教室\n"
                "- **在场**：莉莉丝\n"
                "- **关键词**：教室 重建 记忆\n"
                "- **重要度**：3\n"
                "- **内容**：这是可被 rebuild 的记忆内容。"
            ),
        )

        (tmp_path / "lilith" / ".consolidation_state.json").write_text(
            json.dumps({"last_consolidated_date": "4月4日"}, ensure_ascii=False),
            encoding="utf-8",
        )

        # monkeypatch get_agent_names
        monkeypatch.setattr(vs_mod, "get_agent_names", lambda: ["lilith"])

        from app.memory.indexer import rebuild_memory_index
        monkeypatch.setattr("app.memory.indexer.vector_store", store)
        monkeypatch.setattr("app.memory.indexer.get_agent_names", lambda **_: ["lilith"])
        await rebuild_memory_index()

        res = await wait_for_search(store, "lilith", "被 rebuild 的记忆")
        assert len(res) >= 1, "rebuild 后应该能搜索到 lilith 的记忆"



class TestVectorStoreEdgeCases:
    """边界情况测试"""

    @pytest.mark.asyncio
    async def test_empty_search(self, clean_store):
        """测试空查询返回空候选"""
        store = clean_store

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            rows = store.get_vector_candidates(conn, "lilith", [], 5)
        except Exception:
            rows = []
        finally:
            conn.close()
        assert rows == [], "空向量应该返回空列表"

    @pytest.mark.asyncio
    async def test_search_nonexistent_agent(self, clean_store):
        """测试索引存在时搜索不存在的角色"""
        store = clean_store
        await store.add_episodes(
            [
                EpisodeMemory(
                    date="10月1日",
                    time="早上",
                    location="庭院",
                    participants="莉莉丝",
                    keywords=["测试"],
                    content="莉莉丝记录了一条测试记忆。",
                    memory_owner="lilith",
                )
            ]
        )

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            qvec = embed_sync(["查询"])[0]
            rows = store.get_vector_candidates(conn, "nonexistent", qvec, 5)
        finally:
            conn.close()
        assert rows == [], "不存在的角色应该返回空列表"

    @pytest.mark.asyncio
    async def test_memory_search_isolation(self, clean_store, tmp_path, monkeypatch):
        """测试 memory 层检索：只检索 memory 种类，不同角色互相隔离。"""
        store = clean_store

        write_memory(
            tmp_path,
            "lilith",
            (
                "# lilith 的长期记忆\n\n"
                "## 4月2日\n"
                "- **时间**：4月2日 晚上\n"
                "- **地点**：天台\n"
                "- **在场**：莉莉丝、玩家\n"
                "- **内容**：这是昨天的长期记忆锚点。\n\n"
                "## 4月3日\n"
                "- **时间**：4月3日 上午\n"
                "- **地点**：教室\n"
                "- **在场**：莉莉丝、玩家\n"
                "- **内容**：这是今天的长期记忆，不应进 memory 层。"
            ),
        )

        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月2日"))

        res_old = await wait_for_search(store, "lilith", "昨天的长期记忆锚点")
        assert len(res_old) >= 1, "应该能搜索到昨天的记忆"

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            qvec_today = embed_sync(["今天的长期记忆"])[0]
            res_today_mem = store.get_vector_candidates(conn, "lilith", qvec_today, 5)
            qvec_other = embed_sync(["昨天的长期记忆锚点"])[0]
            res_other = store.get_vector_candidates(conn, "mitsuki", qvec_other, 5)
        finally:
            conn.close()
        assert not any("今天的长期记忆" in r[1] for r in res_today_mem), "4月3日不应在 memory 层"
        assert res_other == [], "mitsuki 不应能看到 lilith 的记忆"

    @pytest.mark.asyncio
    async def test_delete_all_agents_partial(self, clean_store, tmp_path, monkeypatch):
        """测试 delete_all_agents 的逐角色删除路径。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path, "lilith",
            "# lilith\n\n## 4月3日\n- **时间**：4月3日 10:00\n- **地点**：走廊\n- **在场**：莉莉丝\n- **内容**：仅 lilith 可见的记忆。",
        )
        write_memory(
            tmp_path, "mitsuki",
            "# mitsuki\n\n## 4月3日\n- **时间**：4月3日 10:01\n- **地点**：操场\n- **在场**：美月\n- **内容**：仅 mitsuki 可见的记忆。",
        )

        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
        await add_episodes(store, get_episodes(tmp_path, "mitsuki", "4月3日"))

        await wait_for_search(store, "lilith", "仅 lilith 可见的记忆")
        await wait_for_search(store, "mitsuki", "仅 mitsuki 可见的记忆")

        result = await store.delete_all_agents(["lilith"])
        assert result == {"lilith": True}

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            qvec = embed_sync(["仅 lilith 可见的记忆"])[0]
            res_lilith = store.get_vector_candidates(conn, "lilith", qvec, 5)
            qvec2 = embed_sync(["仅 mitsuki 可见的记忆"])[0]
            res_mitsuki = store.get_vector_candidates(conn, "mitsuki", qvec2, 5)
        finally:
            conn.close()
        assert len(res_lilith) == 0, "lilith 的数据应该被删除"
        assert len(res_mitsuki) >= 1, "mitsuki 的数据应该保留"

    @pytest.mark.asyncio
    async def test_delete_all_agents_full_clear(self, clean_store, tmp_path, monkeypatch):
        """测试 delete_all_agents 命中全角色时走全量清理。"""
        import importlib; vs_mod = importlib.import_module("repository.vector_store")

        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path, "lilith",
            "# lilith\n\n## 4月2日\n- **时间**：4月2日 晚上\n- **地点**：天台\n- **在场**：莉莉丝、玩家\n- **内容**：这是昨天的长期记忆锚点。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月2日"))
        await wait_for_search(store, "lilith", "昨天的长期记忆锚点")

        monkeypatch.setattr(vs_mod, "get_agent_names", lambda: ["lilith", "mitsuki", "narrator"])
        result = await store.delete_all_agents(["lilith", "mitsuki", "narrator"])
        assert result == {"lilith": True, "mitsuki": True, "narrator": True}

        db = await store._get_db()
        chunk_count = (await (await db.execute("SELECT COUNT(*) FROM EpisodeMemory")).fetchone())[0]
        vec_count = (await (await db.execute("SELECT COUNT(*) FROM EpisodeMemory_vec")).fetchone())[0]
        assert chunk_count == 0, f"EpisodeMemory 表应该为空，实际有{chunk_count}条"
        assert vec_count == 0, f"vec_EpisodeMemory 表应该为空，实际有{vec_count}条"


class TestVectorStoreMemoryIndexing:
    """长期记忆索引测试"""

    @pytest.mark.asyncio
    async def test_add_splits_events(self, clean_store, tmp_path, monkeypatch):
        """索引指定日期的 memory.md，按事件块拆分并可检索。"""
        store = clean_store

        write_memory(
            tmp_path,
            "mitsuki",
            """
# mitsuki 的长期记忆

## 4月3日
- **时间**：4月3日 上午 08:18 - 08:52
- **地点**：教室
- **在场**：桥本美月、玩家（小明）、莉莉丝（转学生）
- **内容**：转学生莉莉丝到来，她的眼神让我在意。

- **时间**：4月3日 中午 12:00 - 12:30
- **地点**：走廊
- **在场**：桥本美月、玩家（小明）
- **内容**：我找他确认下午的安排。
""".strip(),
        )

        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        await add_episodes(store, get_episodes(tmp_path, "mitsuki", "4月3日"))

        # 等待索引完成并验证结果
        res1 = await wait_for_search(store, "mitsuki", "眼神让我在意")
        res2 = await wait_for_search(store, "mitsuki", "下午的安排")
        assert len(res1) >= 1, "应命中第一个事件块"
        assert len(res2) >= 1, "应命中第二个事件块"

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            qvec = embed_sync(["眼神让我在意"])[0]
            res_other = store.get_vector_candidates(conn, "lilith", qvec, 5)
        finally:
            conn.close()
        assert res_other == [], "他人不可见的 memory 不应返回"

    @pytest.mark.asyncio
    async def test_add_only_targets_date(self, clean_store, tmp_path, monkeypatch):
        """只索引指定日期，其它日期不应被写入。"""
        store = clean_store

        write_memory(
            tmp_path,
            "mitsuki",
            """
# mitsuki 的长期记忆

## 4月3日
- **时间**：4月3日 上午 08:18 - 08:52
- **地点**：教室
- **在场**：桥本美月、玩家（小明）、莉莉丝（转学生）
- **内容**：今天的事让我在意。

## 4月4日
- **时间**：4月4日 上午 09:00 - 09:30
- **地点**：操场
- **在场**：桥本美月
- **内容**：我独自待了一会儿。
""".strip(),
        )

        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        await add_episodes(store, get_episodes(tmp_path, "mitsuki", "4月3日"))

        res_hit = await wait_for_search(store, "mitsuki", "让我在意")
        assert len(res_hit) >= 1, "应命中索引的日期"

        import sqlite3
        conn = sqlite3.connect(test_db_path)
        try:
            VectorStore._load_sqlite_vec_sync(conn)
            qvec = embed_sync(["独自待了一会儿"])[0]
            res_miss = store.get_vector_candidates(conn, "mitsuki", qvec, 5)
        finally:
            conn.close()
        assert not any("独自待了一会儿" in r[1] for r in res_miss), "未索引日期不应返回"

    @pytest.mark.asyncio
    async def test_add_persists_chunk_keywords_and_importance(self, clean_store, tmp_path, monkeypatch):
        """新版 memory.md 中的关键词和重要度应写入 EpisodeMemory。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path,
            "lilith",
            """
# lilith 的长期记忆

## 10月5日
- **时间**：10月5日 晚上
- **地点**：小巷
- **在场**：我、他
- **关键词**：小巷 初次亲密 主动靠近 紧张 心跳
- **重要度**：5
- **内容**：我们在小巷里停下，他第一次主动靠近，试探着吻了我。
""".strip(),
        )

        await add_episodes(store, get_episodes(tmp_path, "lilith", "10月5日"))

        conn = __import__("sqlite3").connect(test_db_path)
        try:
            row = _chunk_row_by_content(conn, "我们在小巷里停下，他第一次主动靠近，试探着吻了我。")
        finally:
            conn.close()

        assert row is not None
        assert row[1] == "小巷、初次亲密、主动靠近、紧张、心跳"
        assert row[2] == 5

    @pytest.mark.asyncio
    async def test_add_appends_same_date_without_replacing_existing(
        self, clean_store, tmp_path, monkeypatch
    ):
        """同一天新增记忆时只追加新行，不重建旧行。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        first = EpisodeMemory(
            date="10月5日",
            time="10月5日 上午",
            location="公司",
            participants="我、他",
            keywords=["公司"],
            importance=2,
            content="上午稳定内容。",
            memory_owner="lilith",
            title="上午",
        )
        second = EpisodeMemory(
            date="10月5日",
            time="10月5日 中午",
            location="公司",
            participants="我、他",
            keywords=["公司", "新增"],
            importance=3,
            content="中午新增内容。",
            memory_owner="lilith",
            title="中午",
        )

        await store.add_episode(first)
        db = await store._get_db()
        await db.execute(
            "UPDATE EpisodeMemory SET last_recalled_at = ? WHERE content = ?",
            ("10月8日", "上午稳定内容。"),
        )
        await db.commit()

        await store.add_episode(second)

        rows = await db.execute_fetchall(
            "SELECT id, content, last_recalled_at "
            "FROM EpisodeMemory WHERE memory_owner = ? AND game_date = ? ORDER BY id",
            ("lilith", "10月5日"),
        )
        assert [row[1:] for row in rows] == [
            ("上午稳定内容。", "10月8日"),
            ("中午新增内容。", "10月5日"),
        ]
        assert rows[0][0] != rows[1][0]


class TestHybridSearch:
    """BM25 + vector 混合 relevance 测试"""

    @pytest.mark.asyncio
    async def test_bm25_search_basic(self, clean_store, tmp_path, monkeypatch):
        """BM25 检索能命中精确关键词。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path, "lilith",
            "# lilith\n\n## 4月3日\n"
            "- **时间**：4月3日 08:00\n- **地点**：教室\n- **在场**：莉莉丝\n"
            "- **内容**：今天遇到了桥本美月，她提到了学园祭的准备工作。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
        await wait_for_search(store, "lilith", "学园祭")

        # 直接测试 BM25 搜索
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(test_db_path)
        store._load_sqlite_vec_sync(conn)
        results = VectorStore.get_bm25_candidates(conn, "lilith", "学园祭", 5)
        conn.close()

        assert len(results) >= 1, "BM25 应命中包含'学园祭'的记忆"
        assert "学园祭" in results[0][1], "第一条结果应包含关键词"

    @pytest.mark.asyncio
    async def test_bm25_search_hits_keywords_column(self, clean_store, tmp_path, monkeypatch):
        """BM25 应能通过 keywords 列命中内容里未直接出现的查询词。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path,
            "lilith",
            "# lilith\n\n## 10月5日\n"
            "- **时间**：10月5日 晚上\n- **地点**：小巷\n- **在场**：我、他\n"
            "- **关键词**：小巷 初次亲密 主动靠近 紧张 心跳\n- **重要度**：5\n"
            "- **内容**：我们在巷子里停下，他第一次主动靠近，试探着吻了我。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "10月5日"))

        conn = __import__("sqlite3").connect(test_db_path)
        try:
            results = VectorStore.get_bm25_candidates(conn, "lilith", "初次亲密", 5)
        finally:
            conn.close()

        assert results, "BM25 应能命中 keywords 列"
        assert "主动靠近" in results[0][1]

    @pytest.mark.asyncio
    async def test_bm25_respects_scope(self, clean_store, tmp_path, monkeypatch):
        """BM25 检索遵循角色可见性隔离。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path, "lilith",
            "# lilith\n\n## 4月3日\n"
            "- **时间**：4月3日 10:00\n- **地点**：走廊\n- **在场**：莉莉丝\n"
            "- **关键词**：紫水晶 传闻\n- **重要度**：3\n"
            "- **内容**：听到了关于紫水晶项链的传闻。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
        await wait_for_search(store, "lilith", "紫水晶")

        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(test_db_path)
        store._load_sqlite_vec_sync(conn)

        # lilith 能搜到
        results_lilith = VectorStore.get_bm25_candidates(conn, "lilith", "紫水晶", 5)
        assert len(results_lilith) >= 1, "lilith 应能搜到自己的记忆"

        # mitsuki 搜不到
        results_mitsuki = VectorStore.get_bm25_candidates(conn, "mitsuki", "紫水晶", 5)
        assert len(results_mitsuki) == 0, "mitsuki 不应看到 lilith 的记忆"

        conn.close()

    @pytest.mark.asyncio
    async def test_hybrid_relevance_fusion_merges_results(self, clean_store, tmp_path, monkeypatch):
        """75% vector + 25% BM25 能合并两路 relevance。"""
        vec_rows = [
            (1, "内容A", 0.2, "4月3日", "4月3日", 3),
            (2, "内容B", 1.0, "4月3日", "4月3日", 3),
        ]
        bm25_rows = [
            (2, "内容B", -5.0, "4月3日", "4月3日", 3),
            (3, "内容C", -3.0, "4月3日", "4月3日", 3),
        ]

        results = hybrid_fusion(vec_rows, bm25_rows)

        ids = [r["id"] for r in results]
        assert ids == ["1", "2", "3"], "vector 占 75% 时，应优先保留更强的向量相关性"
        assert results[0]["relevance"] > results[1]["relevance"] > results[2]["relevance"]

    @pytest.mark.asyncio
    async def test_apply_recency_ranking_reorders(self, clean_store, tmp_path, monkeypatch):
        """apply_recency 应将最近被想起的旧记忆排在前面。"""
        candidates = [
            {
                "id": "1",
                "content": "旧记忆",
                "relevance": 0.5,
                "date": "4月1日",
                "last_recalled_at": "4月8日",
                "importance": 3,
            },
            {
                "id": "2",
                "content": "新记忆",
                "relevance": 0.5,
                "date": "4月6日",
                "last_recalled_at": "4月6日",
                "importance": 3,
            },
        ]
        monkeypatch.setattr(retrieval_module, "RELEVANCE_WEIGHT", 0.7)
        monkeypatch.setattr(retrieval_module, "RECENCY_WEIGHT", 0.3)
        monkeypatch.setattr(retrieval_module, "IMPORTANCE_WEIGHT", 0.0)
        monkeypatch.setattr(retrieval_module, "RECENCY_HALF_LIFE_DAYS", 5.0)
        monkeypatch.setattr(retrieval_module, "RECENCY_DATE_WEIGHT", 0.1)
        monkeypatch.setattr(retrieval_module, "RECENCY_RECALL_WEIGHT", 0.9)
        reordered = apply_recency(candidates, "4月8日")
        assert reordered[0]["id"] == "1", "最近刚被召回的旧记忆应因 recall recency 排在前面"

    def test_importance_score_normalizes_memory_md_scale(self):
        """memory.md 的 1-5 重要度应稳定归一化到 0-1。"""
        assert _importance_score(1) == 0.0
        assert _importance_score(3) == 0.5
        assert _importance_score(5) == 1.0

    @pytest.mark.asyncio
    async def test_apply_recency_ranking_uses_importance(self, clean_store, tmp_path, monkeypatch):
        """在 relevance/recency 相同的情况下，更高重要度应排前。"""
        candidates = [
            {
                "id": "1",
                "content": "普通记忆",
                "relevance": 0.6,
                "date": "4月8日",
                "last_recalled_at": "4月8日",
                "importance": 2,
            },
            {
                "id": "2",
                "content": "关键记忆",
                "relevance": 0.6,
                "date": "4月8日",
                "last_recalled_at": "4月8日",
                "importance": 5,
            },
        ]
        monkeypatch.setattr(retrieval_module, "RELEVANCE_WEIGHT", 0.5)
        monkeypatch.setattr(retrieval_module, "RECENCY_WEIGHT", 0.2)
        monkeypatch.setattr(retrieval_module, "IMPORTANCE_WEIGHT", 0.3)

        reordered = apply_recency(candidates, "4月8日")

        assert reordered[0]["id"] == "2"
        assert reordered[0]["importance_score"] > reordered[1]["importance_score"]

    def test_build_fts_match_query_ignores_markdown_syntax(self):
        """FTS 查询应剔除 Markdown / FTS 特殊字符，避免 MATCH 语法报错。"""
        query = (
            "（深吸了一口气，认真）顾总，我怕我误会，所以直接问了，我们现在是在约会吗？\n"
            "**时间**：10月3日 星期二 18:27\n"
            "**地点**：梧桐街咖啡馆内"
        )

        fts_query = vector_store_module._build_fts_match_query(query)

        assert fts_query
        assert "*" not in fts_query
        assert ":" not in fts_query
        assert '"约会"' in fts_query
        assert " OR " in fts_query

    def test_tokenize_for_fts_preserves_multi_char_cn_words(self):
        """jieba 预分词应尽量保留中文多字词。"""
        tokens = vector_store_module._tokenize_for_fts("我们在小巷里停下脚步").split()

        assert "小巷" in tokens

    @pytest.mark.asyncio
    async def test_recency_signal_weights_take_effect(self, clean_store, tmp_path, monkeypatch):
        """recency_date_weight / recency_recall_weight 应真实参与加权。"""
        monkeypatch.setattr(retrieval_module, "RECENCY_HALF_LIFE_DAYS", 1.0)
        current_game_date = "4月11日"
        event_date = "4月11日"
        last_recalled_at = "4月1日"

        monkeypatch.setattr(retrieval_module, "RECENCY_DATE_WEIGHT", 0.8)
        monkeypatch.setattr(retrieval_module, "RECENCY_RECALL_WEIGHT", 0.2)
        date_heavy = _recency_score(current_game_date, event_date, last_recalled_at)

        monkeypatch.setattr(retrieval_module, "RECENCY_DATE_WEIGHT", 0.2)
        monkeypatch.setattr(retrieval_module, "RECENCY_RECALL_WEIGHT", 0.8)
        recall_heavy = _recency_score(current_game_date, event_date, last_recalled_at)

        assert date_heavy > recall_heavy
        assert 0.75 < date_heavy < 0.85
        assert 0.15 < recall_heavy < 0.25

    @pytest.mark.asyncio
    async def test_bm25_search_handles_markdown_query(self, clean_store, tmp_path, monkeypatch):
        """带 Markdown 场景摘要的 query 不应触发 FTS5 语法错误。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_status(
            tmp_path,
            "narrator",
            "# 故事状态\n\n## 当前时间\n10月3日 18:30\n",
        )
        write_memory(
            tmp_path,
            "lilith",
            "# lilith\n\n## 10月3日\n"
            "- **时间**：10月3日 18:20\n- **地点**：梧桐街咖啡馆\n- **在场**：我、玩家\n"
            "- **内容**：玩家在咖啡馆里直接问我\"我们现在是在约会吗？\"，我没有否认。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "10月3日"))

        conn = __import__("sqlite3").connect(test_db_path)
        try:
            rows = VectorStore.get_bm25_candidates(
                conn,
                "lilith",
                "玩家新消息: （深吸了一口气）我们现在是在约会吗？\n**时间**：10月3日 星期二 18:27",
                5,
            )
        finally:
            conn.close()

        assert rows, "BM25 应能处理带 Markdown 的 query 并返回结果"

    @pytest.mark.asyncio
    async def test_hybrid_search_end_to_end(self, clean_store, tmp_path, monkeypatch):
        """端到端：启用混合检索后 search_memories() 正常返回结果。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(retrieval_module, "HYBRID_SEARCH_ENABLED", True)

        write_memory(
            tmp_path, "lilith",
            "# lilith\n\n## 4月3日\n"
            "- **时间**：4月3日 08:00\n- **地点**：教室\n- **在场**：莉莉丝\n"
            "- **内容**：收到了来自京都大学的录取通知书，非常激动。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
        await wait_for_search(store, "lilith", "录取通知书")

        # 启用混合检索后，用精确关键词搜索
        from app.memory.retrieval import search_memories
        result_str = search_memories("lilith", "京都大学")
        assert result_str != "（无相关记忆）", "混合检索应能命中结果"
        assert "京都大学" in result_str, "结果应包含关键词"

    @pytest.mark.asyncio
    async def test_search_updates_recall_db(self, clean_store, tmp_path, monkeypatch):
        """search_memories() 命中后应更新 SQLite 中的 last_recalled_at。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(retrieval_module, "character_path", make_character_path(tmp_path))

        write_status(
            tmp_path,
            "narrator",
            "# 故事状态\n\n## 当前时间\n4月8日 19:00\n",
        )
        write_memory(
            tmp_path,
            "lilith",
            "# lilith\n\n## 4月3日\n"
            "- **时间**：4月3日 08:00\n- **地点**：教室\n- **在场**：莉莉丝\n"
            "- **内容**：我记住了那天的告白。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
        await wait_for_search(store, "lilith", "告白")

        from app.memory.retrieval import search_memories
        result_str = search_memories("lilith", "告白")
        assert result_str != "（无相关记忆）", "应命中记忆"

        conn = __import__("sqlite3").connect(test_db_path)
        row = conn.execute(
            "SELECT last_recalled_at FROM EpisodeMemory WHERE memory_owner = ? AND content = ?",
            ("lilith", "我记住了那天的告白。"),
        ).fetchone()
        conn.close()
        assert row[0] == "4月8日"

    @pytest.mark.asyncio
    async def test_rebuild_restores_recall_from_memory_jsonl(self, clean_store, tmp_path, monkeypatch):
        """rebuild() 应优先从 memory.jsonl 的 last_recalled_at 恢复 DB 状态。"""
        import importlib
        from repository.memory_store import serialize_episode

        vs_mod = importlib.import_module("repository.vector_store")
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(memory_store_module, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(vs_mod, "get_agent_names", lambda: ["lilith"])

        agent_dir = tmp_path / "lilith"
        agent_dir.mkdir(parents=True, exist_ok=True)
        episode = EpisodeMemory(
            date="4月3日",
            time="4月3日 08:00",
            location="教室",
            participants="莉莉丝",
            keywords=["教室", "recall", "恢复"],
            importance=3,
            content="这是会从 memory.jsonl 恢复 recall 的记忆。",
            memory_owner="lilith",
            last_recalled_at="4月7日",
        )
        (agent_dir / "memory.jsonl").write_text(
            serialize_episode(episode) + "\n",
            encoding="utf-8",
        )

        from app.memory.indexer import rebuild_memory_index
        monkeypatch.setattr("app.memory.indexer.vector_store", store)
        monkeypatch.setattr("app.memory.indexer.get_agent_names", lambda **_: ["lilith"])
        await rebuild_memory_index()

        conn = __import__("sqlite3").connect(test_db_path)
        row = conn.execute(
            "SELECT last_recalled_at FROM EpisodeMemory WHERE memory_owner = ? AND content = ?",
            ("lilith", "这是会从 memory.jsonl 恢复 recall 的记忆。"),
        ).fetchone()
        conn.close()
        assert row[0] == "4月7日"

    @pytest.mark.asyncio
    async def test_rebuild_restores_recall_from_sidecar(self, clean_store, tmp_path, monkeypatch):
        """rebuild() DB 为空时应从 sidecar 降级恢复 last_recalled_at。"""
        import importlib
        import hashlib

        vs_mod = importlib.import_module("repository.vector_store")
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(memory_store_module, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(vs_mod, "get_agent_names", lambda: ["lilith"])

        write_memory(
            tmp_path,
            "lilith",
            "# lilith\n\n## 4月3日\n"
            "- **时间**：4月3日 08:00\n- **地点**：教室\n- **在场**：莉莉丝\n"
            "- **关键词**：教室 recall 恢复\n- **重要度**：3\n"
            "- **内容**：这是会被恢复 recall 的记忆。",
        )
        (tmp_path / "lilith" / ".consolidation_state.json").write_text(
            json.dumps({"last_consolidated_date": "4月4日"}, ensure_ascii=False),
            encoding="utf-8",
        )

        # content_hash 基于 EpisodeMemory.content 原文（vector_store 写入时的哈希算法），
        # 否则 rebuild 读回 JSONL 后与 sidecar 对不上、无法恢复 last_recalled_at
        expected_episode = _read_episodes(tmp_path, "lilith")[0]
        content_hash = hashlib.sha1(expected_episode.content.encode("utf-8")).hexdigest()
        legacy_payload = expected_episode.model_dump(exclude={"last_recalled_at"})
        (tmp_path / "lilith" / "memory.jsonl").write_text(
            json.dumps(legacy_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (tmp_path / "lilith" / ".memory_recall_state.json").write_text(
            json.dumps(
                {
                    "legacy-or-previous-memory-key": {
                        "date": "4月3日",
                        "content_hash": content_hash,
                        "last_recalled_at": "4月7日",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        from app.memory.indexer import rebuild_memory_index
        monkeypatch.setattr("app.memory.indexer.vector_store", store)
        monkeypatch.setattr("app.memory.indexer.get_agent_names", lambda **_: ["lilith"])
        await rebuild_memory_index()

        conn = __import__("sqlite3").connect(test_db_path)
        row = conn.execute(
            "SELECT last_recalled_at FROM EpisodeMemory WHERE memory_owner = ? AND content = ?",
            ("lilith", "这是会被恢复 recall 的记忆。"),
        ).fetchone()
        conn.close()
        assert row[0] == "4月7日"

    @pytest.mark.asyncio
    async def test_fts_sync_on_delete(self, clean_store, tmp_path, monkeypatch):
        """删除角色时 FTS 索引也被清理。"""
        store = clean_store
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))

        write_memory(
            tmp_path, "lilith",
            "# lilith\n\n## 4月3日\n"
            "- **时间**：4月3日 08:00\n- **地点**：教室\n- **在场**：莉莉丝\n"
            "- **内容**：今天讨论了毕业典礼的安排。",
        )
        await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
        await wait_for_search(store, "lilith", "毕业典礼")

        # 确认 FTS 有数据
        db = await store._get_db()
        fts_count_before = (await (await db.execute(
            "SELECT COUNT(*) FROM EpisodeMemory_fts"
        )).fetchone())[0]
        assert fts_count_before > 0, "删除前 FTS 应有数据"

        # 删除
        await store.delete("lilith")

        fts_count_after = (await (await db.execute(
            "SELECT COUNT(*) FROM EpisodeMemory_fts"
        )).fetchone())[0]
        assert fts_count_after == 0, "删除后 FTS 应为空"
