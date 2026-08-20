"""向量库按用户隔离测试。"""
from pathlib import Path

import pytest

# 必须在导入 repository 模块之前加载 .env，避免 EMBED_API_URL/KEY 绑定为空、
# 导致其他向量库测试因收集顺序不同而被误跳过
project_root = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env")
except ImportError:
    pass

from repository.user_context import current_user_id, DEFAULT_USER_ID
from repository.vector_store import VectorStore


@pytest.mark.asyncio
async def test_vector_db_path_scoped_by_user():
    current_user_id.set("vs-user-a")
    va = VectorStore()
    path_a = va._user_db_path()
    current_user_id.set("vs-user-b")
    vb = VectorStore()
    path_b = vb._user_db_path()
    current_user_id.set(DEFAULT_USER_ID)
    await va.close()
    await vb.close()
    assert path_a != path_b
    assert "vs-user-a" in path_a and "vs-user-b" in path_b
