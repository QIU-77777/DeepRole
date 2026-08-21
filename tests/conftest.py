"""Shared test helpers available to all tests in this directory."""

from pathlib import Path

import pytest

# 在任何 repository 模块导入之前加载 .env，避免 EMBED_API_URL/KEY 绑定为空、
# 导致向量库测试因收集顺序不同而被误跳过（与测试文件加载先后无关）
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from app.llm_schema import LLMNarratorOutput
from repository.user_context import DEFAULT_USER_ID, current_user_id


@pytest.fixture
def isolated_user():
    """每个使用它的测试自动落入独立测试用户，结束后恢复默认。"""
    import uuid

    user = f"test-{uuid.uuid4().hex}"
    current_user_id.set(user)
    yield user
    current_user_id.set(DEFAULT_USER_ID)


@pytest.fixture(autouse=True)
def _isolate_users_db(tmp_path_factory, monkeypatch):
    """把 users.db 指向临时目录，避免测试写入真实 data/users.db。"""
    import repository.user_store

    db = tmp_path_factory.mktemp("users") / "users.db"
    monkeypatch.setattr(repository.user_store, "_USERS_DB", db)


@pytest.fixture(autouse=True)
def _isolate_vector_db(tmp_path, monkeypatch):
    """把所有测试的向量库指向临时目录，绝不触碰生产 data/runtime/{user}/vectors.sqlite。

    路径保留 per-user 子目录（{tmp}/{user_id}/vectors.sqlite），与生产结构一致；
    VectorStore 与同步检索（app.memory.retrieval）都动态调用 vector_db_path()，
    因此 patch 两处即可全量隔离。
    返回被 patch 的 vector_db_path，供需要该路径的测试（如 clean_store）复用。
    """
    from repository.user_context import current_user_id
    import repository.vector_store as vs_mod
    import app.memory.retrieval as rt_mod

    def _patched_vector_db_path() -> str:
        return str(tmp_path / current_user_id.get() / "vectors.sqlite")

    monkeypatch.setattr(vs_mod, "vector_db_path", _patched_vector_db_path)
    monkeypatch.setattr(rt_mod, "vector_db_path", _patched_vector_db_path)
    return _patched_vector_db_path


def _narrator_output(**overrides) -> LLMNarratorOutput:
    data = {
        "targets": ["mitsuki"],
        "date": "4月3日 星期三",
        "time": "16:10",
        "location": "走廊",
        "present_characters": {"北原悠": "门口", "美月": "窗边"},
        "scene_description": "走廊里传来广播声。",
        "character_locations": {"北原悠": "走廊", "美月": "走廊"},
        "new_characters": [],
    }
    data.update(overrides)
    return LLMNarratorOutput(**data)
