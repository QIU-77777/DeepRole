# DeepRole 多用户数据隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 DeepRole 从单世界应用变为多用户隔离应用：匿名 token 身份 + 每用户独立数据/向量库/内存态。

**Architecture:** 单进程 uvicorn + FastAPI。身份层：`X-User-Token` 头 → `users.db` 解析 → 写入 `contextvar(current_user_id)`。数据层：`repository/config.py` 的路径函数从 contextvar 派生 `data/runtime/{user_id}/...`；`vector_store`、`character_repo`、`agent_factory`、`consolidation flow`、`server.py` 的全局状态全部改为按 user 分区。前端 localStorage 存 token 并注入请求头。

**Tech Stack:** Python 3.11, FastAPI, uvicorn, contextvars, sqlite3, sqlite-vec, pytest + pytest-asyncio

**工作目录：** `/tmp/agent/deeprole-dev`，分支 `feat/multi-user-isolation`

**测试命令：** `uv run pytest tests/<file> -v`（首次可 `cd /tmp/agent/deeprole-dev && cp ~/python-project/DeepRole/.env .env`）

---

### Task 1: 用户上下文基础设施（contextvar）

**Files:**
- Create: `repository/user_context.py`
- Create: `tests/test_user_context.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_user_context.py
"""contextvar 用户上下文测试。"""
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_default_is_default_user():
    assert current_user_id.get() == DEFAULT_USER_ID


def test_set_and_reset_user():
    token_id = "user-abc"
    current_user_id.set(token_id)
    assert current_user_id.get() == token_id
    current_user_id.set(DEFAULT_USER_ID)
    assert current_user_id.get() == DEFAULT_USER_ID
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_user_context.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'repository.user_context'`

- [ ] **Step 3: 实现**

```python
# repository/user_context.py
"""请求级用户上下文。

FastAPI 依赖把当前请求的 user_id 写入此 contextvar；
repository 层所有路径函数从这里派生数据目录，实现按用户隔离。
"""

from contextvars import ContextVar

DEFAULT_USER_ID = "default"

current_user_id: ContextVar[str] = ContextVar(
    "current_user_id", default=DEFAULT_USER_ID
)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_user_context.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add repository/user_context.py tests/test_user_context.py
git commit -m "feat: add per-request user context (contextvar)"
```

---

### Task 2: users.db 用户存储

**Files:**
- Create: `repository/user_store.py`
- Create: `tests/test_user_store.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_user_store.py
"""users.db token↔user 映射测试。"""
from repository import user_store


def test_create_then_resolve():
    token, user_id = user_store.create_user()
    assert token and user_id
    assert user_store.resolve_user(token) == user_id


def test_resolve_unknown_token_is_none():
    assert user_store.resolve_user("no-such-token") is None


def test_resolve_none_is_none():
    assert user_store.resolve_user(None) is None
    assert user_store.resolve_user("") is None


def test_create_is_unique():
    _, user_id_a = user_store.create_user()
    _, user_id_b = user_store.create_user()
    assert user_id_a != user_id_b
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_user_store.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# repository/user_store.py
"""全局用户表：匿名 token ↔ user_id 映射（data/users.db，不按用户隔离）。"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_USERS_DB = Path(__file__).resolve().parent.parent / "data" / "users.db"


def _connect() -> sqlite3.Connection:
    _USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_USERS_DB))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            token TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def resolve_user(token: str | None) -> str | None:
    """按 token 返回已存在的 user_id；无效/缺失返回 None。"""
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT user_id FROM users WHERE token = ?", (token,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def create_user() -> tuple[str, str]:
    """创建新用户，返回 (token, user_id)。"""
    token = uuid.uuid4().hex
    user_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO users (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()
    return token, user_id
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_user_store.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 清理测试副作用并提交**

```bash
rm -f /tmp/agent/deeprole-dev/data/users.db
cd /tmp/agent/deeprole-dev
git add repository/user_store.py tests/test_user_store.py
git commit -m "feat: add global users.db token mapping"
```

---

### Task 3: config.py 路径函数化（隔离核心）

**Files:**
- Modify: `repository/config.py`
- Modify: `repository/runtime_state.py`
- Modify: `repository/save_manager.py`（CHARACTERS_DIR 引用点 + 存档路径）
- Modify: `server.py`（import 与 `_LAST_CHOICES_FILE`）
- Modify: `tests/test_user_context.py`

- [ ] **Step 1: 写失败测试（追加到 test_user_context.py）**

```python
# tests/test_user_context.py（追加）
"""路径隔离测试：不同 user 指向不同目录。"""
from pathlib import Path

from repository.config import character_path, characters_dir, runtime_dir
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_runtime_dir_scopes_by_user():
    current_user_id.set("user-a")
    dir_a = runtime_dir()
    current_user_id.set("user-b")
    dir_b = runtime_dir()
    current_user_id.set(DEFAULT_USER_ID)
    assert "user-a" in str(dir_a)
    assert "user-b" in str(dir_b)
    assert dir_a != dir_b
    assert dir_a.name == "user-a" and dir_b.name == "user-b"


def test_character_path_scopes_by_user():
    current_user_id.set("user-a")
    path_a = character_path("narrator", "soul.md")
    current_user_id.set("user-b")
    path_b = character_path("narrator", "soul.md")
    current_user_id.set(DEFAULT_USER_ID)
    assert path_a != path_b
    assert "user-a" in path_a and "user-b" in path_b
    assert path_a.endswith("characters/narrator/soul.md")


def test_characters_dir_scoped():
    current_user_id.set("user-c")
    d = characters_dir()
    current_user_id.set(DEFAULT_USER_ID)
    assert d.name == "characters"
    assert "user-c" in str(d)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_user_context.py -v`
Expected: FAIL（character_path 仍是旧函数，无 user 维度）

- [ ] **Step 3: 实现 config.py**

```python
# repository/config.py 修改要点：
# 删除模块常量 RUNTIME_DIR / CHARACTERS_DIR（第 15-16 行），改为函数；character_path 加 user 维度。
import tomllib
from pathlib import Path

from repository.user_context import current_user_id

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def runtime_dir() -> Path:
    """当前用户的运行时数据目录：data/runtime/{user_id}。"""
    return PROJECT_ROOT / "data" / "runtime" / current_user_id.get()


def characters_dir() -> Path:
    """当前用户的角色目录：data/runtime/{user_id}/characters。"""
    return runtime_dir() / "characters"


def character_path(character_name: str, *subpaths: str) -> str:
    """构建当前用户下角色的数据路径。"""
    return str(characters_dir() / character_name / Path(*subpaths))


def get_agent_names(include_narrator: bool = True) -> list[str]:
    chars_dir = characters_dir()
    if not chars_dir.exists():
        return []
    agents = sorted(
        d.name
        for d in chars_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_")
    )
    if not include_narrator:
        agents = [name for name in agents if name != "narrator"]
    return agents
```

- [ ] **Step 4: 修复所有 `CHARACTERS_DIR` / `RUNTIME_DIR` 引用点**

**`repository/runtime_state.py`**：`from repository.config import CHARACTERS_DIR` → `from repository.config import characters_dir`；文件内所有 `CHARACTERS_DIR` → `characters_dir()`；`read_player_name` 去掉 `@cache` 装饰器（防止跨用户串缓存）。

**`repository/save_manager.py`**：
- `from repository.config import CHARACTERS_DIR, PROJECT_ROOT, character_path, get_agent_names` → 去掉 `CHARACTERS_DIR`，改为 `characters_dir, runtime_dir`
- 文件内 `CHARACTERS_DIR` → `characters_dir()`（约 line 119, 641, 650, 655, 661, 822, 831）
- 存档路径改为按用户：`PROJECT_ROOT / "saves"` → `runtime_dir() / "saves"`（line 287, 413, 465, 512），并确保 `os.makedirs(save_dir, exist_ok=True)` 在写前存在

**`server.py`**：
- `from repository.config import CHARACTERS_DIR, get_agent_names` → `from repository.config import characters_dir, get_agent_names`
- 删除 `_LAST_CHOICES_FILE = CHARACTERS_DIR / "last_choices.json"`（line 69），改为函数：

```python
def _last_choices_file() -> Path:
    return characters_dir() / "last_choices.json"
```

- `_save_last_choices` / `_load_last_choices` / `_clear_last_choices` 内部用 `_last_choices_file()` 替换 `_LAST_CHOICES_FILE`

- [ ] **Step 5: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_user_context.py -v`
Expected: PASS（6 passed，原 3 + 新 3）

Run: `cd /tmp/agent/deeprole-dev && uv run python -c "import server"` 确认 import 无错。

- [ ] **Step 6: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add -A
git commit -m "feat: scope data paths by current user via contextvar"
```

---

### Task 4: vector_store per-user

**Files:**
- Modify: `repository/vector_store.py`
- Modify: `tests/test_vector_store.py`（适配 contextvar）

- [ ] **Step 1: 写失败测试（追加 tests/test_vector_store.py）**

```python
# tests/test_vector_store.py（文件末尾追加）
"""向量库按用户隔离测试。"""
from repository.user_context import current_user_id, DEFAULT_USER_ID
from repository.vector_store import VectorStore


@pytest.mark.skipif(
    not EMBED_API_URL or not EMBED_API_KEY,
    reason="EMBEDDING_API_URL 或 EMBEDDING_API_KEY 未配置，跳过测试",
)
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_vector_store.py::test_vector_db_path_scoped_by_user -v`
Expected: FAIL（`_user_db_path` 不存在）

- [ ] **Step 3: 实现**

```python
# repository/vector_store.py 修改要点：
# 1. 删除模块常量 DB_PATH（line 43）
# 2. VectorStore.__init__：self._db 单连接改为 per-user dict
# 3. 新增 _user_db_path() 与 _user_key()
# 4. _get_db() / close() / 相关方法按当前 user 解析

class VectorStore:
    def __init__(self):
        self._dbs: dict[str, aiosqlite.Connection] = {}

    def _user_key(self) -> str:
        from repository.user_context import current_user_id
        return current_user_id.get()

    def _user_db_path(self) -> str:
        from repository.config import runtime_dir
        return str(runtime_dir() / "vectors.sqlite")

    async def _get_db(self) -> aiosqlite.Connection:
        key = self._user_key()
        conn = self._dbs.get(key)
        if conn is not None:
            return conn
        path = self._user_db_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = await aiosqlite.connect(path)
        ...  # 原有 schema 初始化逻辑（原本在 _get_db 里）保持
        self._dbs[key] = db
        return db

    async def close(self) -> None:
        for conn in self._dbs.values():
            if conn is not None:
                await conn.close()
        self._dbs = {}
```

> 注意：原 `_get_db()` 中创建表/load_extension 的逻辑保留不动，只把"单连接缓存"改为 dict 按 user 缓存、路径改为 `_user_db_path()`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_vector_store.py::test_vector_db_path_scoped_by_user -v`
Expected: PASS

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_vector_store.py -v`（现有回归；若现有测试用旧 test_db_path 依赖 `_db` 属性，需把测试中的 `vector_store._db` 访问改为 `_dbs`）

- [ ] **Step 5: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add repository/vector_store.py tests/test_vector_store.py
git commit -m "feat: per-user vector store connection and db path"
```

---

### Task 5: character_repo per-user 缓存

**Files:**
- Modify: `repository/character_repo.py`
- Modify: `tests/test_character_repo.py`（若无则新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_character_repo.py（新建）
"""character_repo soul 缓存按用户隔离。"""
from repository.character_repo import CharacterRepository
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_soul_cache_scoped_by_user(tmp_path, monkeypatch):
    repo = CharacterRepository()
    current_user_id.set("cr-user-a")
    cache_key_a = id(repo._soul_cache)
    current_user_id.set("cr-user-b")
    cache_key_b = id(repo._soul_cache)
    current_user_id.set(DEFAULT_USER_ID)
    assert cache_key_a != cache_key_b
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_character_repo.py -v`
Expected: FAIL（单缓存，id 相同）

- [ ] **Step 3: 实现**

```python
# repository/character_repo.py 修改要点：
class CharacterRepository:
    def __init__(self) -> None:
        self._soul_cache: dict[str, dict[str, str]] = {}

    def _user_cache(self) -> dict[str, str]:
        from repository.user_context import current_user_id
        return self._soul_cache.setdefault(current_user_id.get(), {})

    def load(self, name: str) -> Character:
        cache = self._user_cache()
        soul = cache.get(name)
        if soul is None:
            soul = read_agent_file(name, "soul.md")
            cache[name] = soul
        return Character(name=name, soul=soul)

    def invalidate(self, name: str | None = None) -> None:
        from repository.user_context import current_user_id
        cache = self._soul_cache.setdefault(current_user_id.get(), {})
        if name is None:
            cache.clear()
        else:
            cache.pop(name, None)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_character_repo.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add repository/character_repo.py tests/test_character_repo.py
git commit -m "feat: scope character soul cache per user"
```

---

### Task 6: agent_factory _conversation_agents per-user

**Files:**
- Modify: `app/agent_factory.py`
- Modify: `tests/test_agent_factory.py`

- [ ] **Step 1: 写失败测试（追加 tests/test_agent_factory.py）**

```python
# tests/test_agent_factory.py（追加）
"""conversation agents 按用户分区。"""
from app import agent_factory
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_conversation_agents_scoped_by_user():
    current_user_id.set("af-user-a")
    agent_factory._conversation_agents.clear()
    agent_factory.reload_conversation_agent("narrator")
    keys_a = set(agent_factory._conversation_agents)
    current_user_id.set("af-user-b")
    agent_factory.reload_conversation_agent("narrator")
    keys_b = set(agent_factory._conversation_agents)
    current_user_id.set(DEFAULT_USER_ID)
    assert "af-user-a" in keys_a
    assert "af-user-b" in keys_b
    assert agent_factory._conversation_agents["af-user-a"] is not agent_factory._conversation_agents["af-user-b"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_agent_factory.py::test_conversation_agents_scoped_by_user -v`
Expected: FAIL（`_conversation_agents` 是 `dict[str, Agent]`，key 是角色名）

- [ ] **Step 3: 实现**

```python
# app/agent_factory.py 修改要点：
# 1. _conversation_agents: dict[str, dict[str, ConversationAgent]] = {}  # user_id -> name -> agent
# 2. _user_key() 帮助函数
# 3. reload_conversation_agent / get_conversation_agent 按 user 分区
# 4. initialize_conversation_agents() 改为在当前 contextvar 用户下初始化（startup 无 user 时初始化 default）

_conversation_agents: dict[str, dict[str, ConversationAgent]] = {}


def _user_key() -> str:
    from repository.user_context import current_user_id
    return current_user_id.get()


def reload_conversation_agent(name: str) -> None:
    global _choices_agent
    user = _user_key()
    agents = _conversation_agents.setdefault(user, {})
    soul = read_agent_file(name, "soul.md")
    config = get_llm_config()
    output_type = LLMNarratorOutput if name == "narrator" else LLMCharacterOutput
    agents[name] = _build_agent(
        name=name,
        instructions=build_system_prompt(name, soul),
        config=config,
        output_type=output_type,
    )
    if name == "narrator":
        _choices_agent = None


def get_conversation_agent(name: str) -> ConversationAgent:
    agents = _conversation_agents.setdefault(_user_key(), {})
    if name not in agents:
        reload_conversation_agent(name)
    return agents[name]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_agent_factory.py::test_conversation_agents_scoped_by_user -v`
Expected: PASS

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_agent_factory.py -v`（现有回归，reload 在无 LLM 配置时可能失败——若现有测试已能跑则保持）

- [ ] **Step 5: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add app/agent_factory.py tests/test_agent_factory.py
git commit -m "feat: scope conversation agents per user"
```

---

### Task 7: consolidation flow per-user

**Files:**
- Modify: `app/consolidation/flow.py`
- Modify: `tests/test_server_state_updater.py`（或新建隔离测试）

- [ ] **Step 1: 读 flow.py 确认所有实例状态字段**

Run: `cd /tmp/agent/deeprole-dev && grep -n '_locks\|_active_count\|_scheduled_task\|_pending_turn\|last_created_episodes' app/consolidation/flow.py`

- [ ] **Step 2: 实现（按 user 分区所有状态字段）**

```python
# app/consolidation/flow.py 修改要点（__init__ 与所有用到这些字段的方法）：
class MemoryConsolidationFlow:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}          # f"{user_id}:{agent_name}"
        self._active_by_user: dict[str, int] = {}
        self._scheduled_by_user: dict[str, asyncio.Task[None] | None] = {}
        self._pending_turn_by_user: dict[str, int | None] = {}
        self.last_created_episodes_by_user: dict[str, list] = {}

    def _user_key(self) -> str:
        from repository.user_context import current_user_id
        return current_user_id.get()

    def _get_lock(self, agent_name: str) -> asyncio.Lock:
        key = f"{self._user_key()}:{agent_name}"
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    @property
    def is_running(self) -> bool:
        return self._active_by_user.get(self._user_key(), 0) > 0

    @property
    def last_created_episodes(self) -> list:
        return self.last_created_episodes_by_user.get(self._user_key(), [])

    @last_created_episodes.setter
    def last_created_episodes(self, value) -> None:
        self.last_created_episodes_by_user[self._user_key()] = value
```

> 其余方法（`schedule_detect_and_consolidate`、`_consolidation_pipeline` 等）中对 `self._active_count`、`self._scheduled_task`、`self._pending_turn` 的引用，一律改为对应的 `*_by_user[self._user_key()]`。

- [ ] **Step 3: 运行现有 consolidation 相关测试确认不回归**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_server_state_updater.py -v`
Expected: PASS（或按实际失败修正字段名引用）

- [ ] **Step 4: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add app/consolidation/flow.py
git commit -m "feat: scope consolidation flow state per user"
```

---

### Task 8: server.py 身份依赖 + 全局态 per-user + /api/me

**Files:**
- Modify: `server.py`
- Create: `tests/test_server_isolation.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_server_isolation.py（新建）
"""API 级隔离测试：两个 token 各自独立世界。"""
import httpx
import pytest_asyncio
from httpx import ASGITransport
from fastapi import FastAPI

from server import app
from repository.user_context import current_user_id, DEFAULT_USER_ID


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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_server_isolation.py -v`
Expected: FAIL（`/api/me` 404）

- [ ] **Step 3: 实现 server.py 身份层**

```python
# server.py 新增（放在工具函数区之后）：

from fastapi import HTTPException, Request
from repository import user_store
from repository.user_context import current_user_id
from repository.config import characters_dir
from repository.save_manager import reset_game


def require_user(request: Request) -> str:
    """FastAPI 依赖：解析 X-User-Token → 设 contextvar → 返回 user_id。"""
    token = request.headers.get("X-User-Token")
    user_id = user_store.resolve_user(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="无效的访问凭证，请刷新页面重新进入。")
    current_user_id.set(user_id)
    return user_id


async def _ensure_user_world(user_id: str) -> None:
    """新用户首次进入时从模板初始化默认世界。"""
    if not characters_dir().exists():
        await reset_game("drama")


@app.get("/api/me")
async def api_me(request: Request) -> JSONResponse:
    """获取当前用户；无 token 时创建新用户并初始化默认世界。"""
    token = request.headers.get("X-User-Token")
    user_id = user_store.resolve_user(token)
    if user_id is None:
        token, user_id = user_store.create_user()
        current_user_id.set(user_id)
        await _ensure_user_world(user_id)
    else:
        current_user_id.set(user_id)
    return JSONResponse({"token": token, "user_id": user_id})
```

- [ ] **Step 4: 所有业务路由加 `Depends(require_user)`**

对以下路由的签名追加参数 `_: str = Depends(require_user)`（参数名用 `_` 避免 shadow）：
`api_init`、`api_history`、`api_history_dates`、`api_history_search`、`api_stories`、`api_memory_graph`、`api_new_game`、`api_chat`、`api_status`、`api_list_saves`、`api_save`、`api_load`、`api_delete_save_node`、`api_delete_save`、`api_reset`、`api_characters`

示例：
```python
@app.get("/api/init")
async def api_init(_: str = Depends(require_user)) -> JSONResponse:
    ...
```

- [ ] **Step 5: server.py 全局态 per-user**

```python
# 替换 line 70-73 的全局单例：
_pending_state_update_task: dict[str, asyncio.Task[None] | None] = {}
_pending_state_update_requested: dict[str, bool] = {}
_pending_choices_task: dict[str, asyncio.Task[list[str]] | None] = {}
_choices_generation_token: dict[str, int] = {}
```

`_invalidate_pending_choices`、`_settle_pending_state_update`、`_run_state_update_loop`、`_start_state_update`、`_chat_stream` 中所有对这些全局变量的读写，均改为以 `current_user_id.get()` 为 key 的 dict 操作。

`_get_agent_display_name`（line 123 `@lru_cache`）去掉 `@lru_cache` 装饰器——每次计算（soul 读取走 character_repo 缓存），避免跨用户串缓存。

- [ ] **Step 6: 运行确认通过**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_server_isolation.py -v`
Expected: PASS

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/test_server_state_updater.py -v`（回归）

Run: `cd /tmp/agent/deeprole-dev && uv run python -c "import server"`（import 无错）

- [ ] **Step 7: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add server.py tests/test_server_isolation.py
git commit -m "feat: per-user request identity, global state partitioning, /api/me"
```

---

### Task 9: 前端 app.js token

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: 实现 token 注入**

```javascript
// static/app.js 修改要点：
// 1. 在 Alpine data 对象里加 state：token: null
// 2. init() 开头调用 this.ensureToken()
// 3. 新增 ensureToken() 方法
// 4. fetchJson 统一注入 X-User-Token 头

async ensureToken() {
  if (this.token) return;
  const stored = localStorage.getItem("deeprole_token");
  if (stored) {
    this.token = stored;
    return;
  }
  const response = await fetch("/api/me");
  const data = await response.json();
  this.token = data.token;
  localStorage.setItem("deeprole_token", this.token);
},

async fetchJson(url, options = {}) {
  if (!this.token) await this.ensureToken();
  const headers = {
    ...(options.headers || {}),
    "X-User-Token": this.token,
  };
  const response = await fetch(url, { ...options, headers });
  // ...原有错误处理保持不动
},
```

`init()` 在第一个 `fetchJson` 前调用 `await this.ensureToken()`（如 `init()` 开头 `await this.ensureToken();`）。

- [ ] **Step 2: 手动验证**

在开发目录启动服务：`uv run uvicorn server:app --port 8000`，浏览器打开 `http://127.0.0.1:8000/`，打开 DevTools → Network，确认：
- 首个请求是 `/api/me`，返回 token
- 后续请求均带 `X-User-Token` 头
- localStorage 出现 `deeprole_token`

（无法自动测试前端，此步为人工验收。）

- [ ] **Step 3: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add static/app.js
git commit -m "feat: frontend token acquisition and header injection"
```

---

### Task 10: 测试完善与全量回归

**Files:**
- Modify: `tests/conftest.py`
- Run 全量测试

- [ ] **Step 1: 扩展 conftest 提供用户隔离 fixture**

```python
# tests/conftest.py（追加）
"""Shared test helpers available to all tests in this directory."""
import pytest

from repository.user_context import current_user_id, DEFAULT_USER_ID


@pytest.fixture
def isolated_user():
    """每个测试自动落入独立测试用户，结束后恢复默认。"""
    import uuid
    user = f"test-{uuid.uuid4().hex}"
    current_user_id.set(user)
    yield user
    current_user_id.set(DEFAULT_USER_ID)


@pytest.fixture
def reset_users_db(tmp_path, monkeypatch):
    """把 users.db 指向临时目录，避免污染真实 data/users.db。"""
    import repository.user_store
    monkeypatch.setattr(repository.user_store, "_USERS_DB", tmp_path / "users.db")
```

- [ ] **Step 2: 运行全量测试并修复回归**

Run: `cd /tmp/agent/deeprole-dev && uv run pytest tests/ -x -v 2>&1 | tail -60`
Expected: 全部通过（或修复因路径函数化/缓存改造导致的回归；注意需要 `.env` 与 embedding key 的测试可能 skip）

- [ ] **Step 3: 提交**

```bash
cd /tmp/agent/deeprole-dev
git add tests/conftest.py
git commit -m "test: add user-isolation fixtures and regression fixes"
```

---

### Task 11: 服务器部署更新

**Files:**
- 服务器 `/var/www/DeepRole`

- [ ] **Step 1: 推送分支并合并 main**

```bash
cd /tmp/agent/deeprole-dev
git push origin feat/multi-user-isolation
git checkout main && git merge feat/multi-user-isolation --no-edit
git push origin main
```

- [ ] **Step 2: 服务器拉取并重启**

```bash
workbench exec --instance-id i-bp13sgdgi8d2tluk291e --timeout 120 --command \
  "cd /var/www/DeepRole && git pull && systemctl restart deeprole && sleep 5 && systemctl is-active deeprole"
```

- [ ] **Step 3: 服务器端冒烟验证**

```bash
# 无 token 获取新用户
TOKEN=$(curl -s http://127.0.0.1:8000/api/me | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
# 用 token 访问 /api/init，确认 200 且返回独立世界
curl -s -H "X-User-Token: $TOKEN" -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/init
# 公网验证
curl -s -o /dev/null -w "%{http_code}\n" http://121.43.49.63:8080/
```

Expected: `200` 全部

- [ ] **Step 4: 浏览器验收**

浏览器打开 `http://121.43.49.63:8080/`：
- 无痕窗口开两个，各自进入游戏
- 两个窗口的世界/对话/存档互不可见
- 刷新页面（同 token）状态保持

---

## Self-Review

**Spec 覆盖：**
- §1 身份与 Token → Task 2 + Task 8（/api/me）
- §2 数据目录隔离 → Task 3 + Task 4（含存档 saves 路径）
- §3 内存态隔离 → Task 5, 6, 7, 8
- §4 新用户初始化 → Task 8（`_ensure_user_world`）
- §5 前端改动 → Task 9
- §6 并发与安全 → 单进程保持；user_id 为服务端 uuid，无路径穿越
- §7 测试 → Task 1, 2, 4, 5, 6, 8, 10

**类型一致性：** `_user_key()`、`runtime_dir()`、`characters_dir()`、`current_user_id` 在各 Task 中签名一致。
