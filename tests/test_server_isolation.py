"""API 级隔离测试：两个 token 各自独立世界。"""
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from server import app
from repository.user_context import current_user_id, DEFAULT_USER_ID


@pytest.fixture(autouse=True)
def _isolate_runtime_world(tmp_path, monkeypatch):
    """把用户运行时目录指到临时目录，避免 /api/me 初始化世界污染 data/runtime/。

    character_path / get_agent_names 内部都通过 config.characters_dir 动态取路径，
    因此只需 patch 各模块直接绑定的 characters_dir / runtime_dir 引用即可全量隔离。
    """
    import repository.config as config_mod
    import repository.runtime_state as runtime_state_mod
    import repository.save_manager as save_manager
    import server as server_module

    def _runtime_dir():
        return tmp_path / "runtime" / current_user_id.get()

    def _characters_dir():
        return _runtime_dir() / "characters"

    monkeypatch.setattr(config_mod, "runtime_dir", _runtime_dir)
    monkeypatch.setattr(config_mod, "characters_dir", _characters_dir)
    monkeypatch.setattr(save_manager, "runtime_dir", _runtime_dir)
    monkeypatch.setattr(save_manager, "characters_dir", _characters_dir)
    monkeypatch.setattr(server_module, "characters_dir", _characters_dir)
    monkeypatch.setattr(runtime_state_mod, "characters_dir", _characters_dir)
    monkeypatch.setattr(save_manager, "reset_logs", lambda: None)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_me_creates_distinct_users(client):
    r1 = await client.get("/api/me")
    r2 = await client.get("/api/me")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["user_id"] != r2.json()["user_id"]


@pytest.mark.asyncio
async def test_me_returns_same_user_for_same_token(client):
    r1 = await client.get("/api/me")
    token = r1.json()["token"]
    headers = {"X-User-Token": token}
    r2 = await client.get("/api/me", headers=headers)
    assert r2.json()["user_id"] == r1.json()["user_id"]


@pytest.mark.asyncio
async def test_api_requires_valid_token(client):
    r = await client.get("/api/init")
    assert r.status_code == 401
