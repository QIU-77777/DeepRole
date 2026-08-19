"""测试游戏存档加载功能中向量索引的一致性

验证思路：
1. 初始状态：向量数据库中已有数据
2. 保存操作：执行 save 操作
3. 加载操作：执行 load 操作
4. 验证：对比 load 后的数据库内容是否与 save 前的数据库内容完全一致
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# 设置项目根目录
project_root = Path(__file__).parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")
except ImportError:
    pass

# 导入必要模块
try:
    import importlib
    import repository.vector_store
    import repository.save_manager as save_manager_module
    import app.memory.retrieval
    import repository.memory_store as memory_store_module
    vector_store_module = importlib.import_module("repository.vector_store")
    retrieval_module = importlib.import_module("app.memory.retrieval")
    from repository.vector_store import vector_store
    from repository.llm.embedding import EMBED_API_URL, EMBED_API_KEY
    from repository.save_manager import export_save_archive, import_save_archive
    from models import EpisodeMemory
    from repository.memory_store import serialize_episode, parse_jsonl_line
except ModuleNotFoundError as exc:
    pytest.skip(f"skip save_load tests: missing dependency ({exc})", allow_module_level=True)

# 检查 embedding 配置
pytestmark = pytest.mark.skipif(
    not EMBED_API_URL or not EMBED_API_KEY,
    reason="EMBEDDING_API_URL 或 EMBEDDING_API_KEY 未配置，跳过测试"
)


def make_character_path(tmp_path):
    def _path(name, subpath=None):
        base = tmp_path / name
        if subpath:
            return str(base / subpath)
        return str(base)
    return _path


def _parse_memory_markdown(content: str, memory_owner: str = "") -> list[EpisodeMemory]:
    """把测试里惯用的 memory.md 字符串解析成 EpisodeMemory 列表。"""
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
    episodes = _parse_memory_markdown(content, memory_owner=agent_name)
    with path.open("w", encoding="utf-8") as f:
        for ep in episodes:
            f.write(serialize_episode(ep) + "\n")
    return path


def get_episodes(tmp_path, agent_name: str, date: str) -> list[EpisodeMemory]:
    """从 tmp_path 下的 memory.jsonl 提取指定日期的 EpisodeMemory 列表。"""
    path = tmp_path / agent_name / "memory.jsonl"
    if not path.exists():
        return []
    episodes: list[EpisodeMemory] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = parse_jsonl_line(line)
        if record is None or record.date != date:
            continue
        episodes.append(record)
    return episodes


async def add_episodes(store, episodes: list[EpisodeMemory]) -> None:
    for episode in episodes:
        await store.add_episode(episode)


_MEMORY_CHUNK_COLS = [
    "id",
    "memory_owner",
    "game_date",
    "title",
    "time",
    "location",
    "participants",
    "content",
    "keywords",
    "importance",
    "content_hash",
    "last_recalled_at",
]


def _get_db_snapshot(db_path: str) -> dict:
    """获取数据库快照：EpisodeMemory 与 EpisodeMemory_vec 的内容"""
    if not os.path.exists(db_path):
        return {"EpisodeMemory": [], "EpisodeMemory_vec": []}

    conn = sqlite3.connect(db_path)
    try:
        try:
            import sqlite_vec
            ext_path = sqlite_vec.loadable_path()
            conn.enable_load_extension(True)
            conn.execute(f"SELECT load_extension('{ext_path}')")
        except Exception:
            pass

        rows = conn.execute(
            "SELECT id, memory_owner, game_date, title, time, location, "
            "participants, content, keywords, importance, content_hash, last_recalled_at "
            "FROM EpisodeMemory ORDER BY id"
        ).fetchall()

        vec_count = conn.execute("SELECT COUNT(*) FROM EpisodeMemory_vec").fetchone()[0]

        return {
            "EpisodeMemory": [dict(zip(_MEMORY_CHUNK_COLS, row)) for row in rows],
            "EpisodeMemory_vec": [{"rowid": i} for i in range(vec_count)],
        }
    finally:
        conn.close()


def _compare_snapshots(before: dict, after: dict) -> tuple[bool, str]:
    """比较两个数据库快照，返回 (是否一致, 差异描述)"""
    if len(before["EpisodeMemory"]) != len(after["EpisodeMemory"]):
        return False, f"EpisodeMemory 数量不同: before={len(before['EpisodeMemory'])}, after={len(after['EpisodeMemory'])}"

    if len(before["EpisodeMemory_vec"]) != len(after["EpisodeMemory_vec"]):
        return False, f"EpisodeMemory_vec 数量不同: before={len(before['EpisodeMemory_vec'])}, after={len(after['EpisodeMemory_vec'])}"

    # 比较 EpisodeMemory 内容（忽略存储层 id，重建后允许重新分配）
    for i, (b, a) in enumerate(zip(before["EpisodeMemory"], after["EpisodeMemory"])):
        b_copy = {k: v for k, v in b.items() if k != "id"}
        a_copy = {k: v for k, v in a.items() if k != "id"}
        if b_copy != a_copy:
            return False, f"EpisodeMemory[{i}] 不同: before={b_copy}, after={a_copy}"

    return True, "数据库内容完全一致"


class TestSaveLoadConsistency:
    """测试 save-load 循环中向量索引的一致性"""

    @pytest.mark.asyncio
    async def test_vector_index_consistency_after_save_load(self, tmp_path, monkeypatch):
        """验证 save-load 循环后向量索引一致性"""
        test_db_path = str(tmp_path / "test_vectors.sqlite")
        monkeypatch.setattr(vector_store_module, "vector_db_path", lambda: test_db_path)
        monkeypatch.setattr(retrieval_module, "vector_db_path", lambda: test_db_path)

        store = vector_store
        await store.close()
        monkeypatch.setattr(store, "character_path", make_character_path(tmp_path))
        monkeypatch.setattr(save_manager_module, "character_path", make_character_path(tmp_path))

        try:
            write_memory(
                tmp_path,
                "lilith",
                "# lilith\n\n## 4月3日\n"
                "- **时间**：4月3日 09:00\n- **地点**：教室\n- **在场**：莉莉丝\n"
                "- **内容**：这是第一轮对话的内容，包含重要信息。",
            )
            await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))

            write_memory(
                tmp_path,
                "mitsuki",
                "# mitsuki\n\n## 4月3日\n"
                "- **时间**：4月3日 09:30\n- **地点**：走廊\n- **在场**：美月\n"
                "- **内容**：这是第二轮对话的内容，mitsuki 的回应。",
            )
            await add_episodes(store, get_episodes(tmp_path, "mitsuki", "4月3日"))

            from app.memory.retrieval import search_memories
            search_result = search_memories("lilith", "第一轮对话")
            assert search_result != "（无相关记忆）", "save 前应该能搜索到数据"

            snapshot_before = _get_db_snapshot(test_db_path)
            assert len(snapshot_before["EpisodeMemory"]) == 2, "应该有 2 条记忆"
            assert len(snapshot_before["EpisodeMemory_vec"]) == 2, "应该有 2 条向量"

            # 模拟 save：把 DB 中最新 last_recalled_at 合并进 archive 里的 memory.jsonl
            for agent_name in ["lilith", "mitsuki"]:
                recall_state = await store.export_recall_state(agent_name)
                payload = save_manager_module._memory_jsonl_archive_payload(
                    agent_name,
                    recall_state,
                )
                if payload is not None:
                    (tmp_path / agent_name / "memory.jsonl").write_text(
                        payload,
                        encoding="utf-8",
                    )

            # 模拟 load：清空数据库后从 archive 内容重建
            db = await store._get_db()
            await db.execute("DELETE FROM EpisodeMemory_vec")
            await db.execute("DELETE FROM EpisodeMemory")
            await db.commit()

            snapshot_empty = _get_db_snapshot(test_db_path)
            assert len(snapshot_empty["EpisodeMemory"]) == 0, "清空后应该没有记忆"
            assert len(snapshot_empty["EpisodeMemory_vec"]) == 0, "清空后应该没有向量"

            # 重新加载相同的数据（模拟 rebuild）
            await add_episodes(store, get_episodes(tmp_path, "lilith", "4月3日"))
            await add_episodes(store, get_episodes(tmp_path, "mitsuki", "4月3日"))

            snapshot_after = _get_db_snapshot(test_db_path)

            assert len(snapshot_after["EpisodeMemory"]) == len(snapshot_before["EpisodeMemory"]), \
                f"EpisodeMemory 数量不一致: before={len(snapshot_before['EpisodeMemory'])}, after={len(snapshot_after['EpisodeMemory'])}"
            assert len(snapshot_after["EpisodeMemory_vec"]) == len(snapshot_before["EpisodeMemory_vec"]), \
                f"EpisodeMemory_vec 数量不一致: before={len(snapshot_before['EpisodeMemory_vec'])}, after={len(snapshot_after['EpisodeMemory_vec'])}"

            is_consistent, message = _compare_snapshots(snapshot_before, snapshot_after)
            assert is_consistent, f"save-load 后数据库不一致: {message}"

            search_result_after = search_memories("lilith", "第一轮对话")
            assert search_result_after != "（无相关记忆）", "load 后应该能搜索到数据"
        finally:
            await store.close()
