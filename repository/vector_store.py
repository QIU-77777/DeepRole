"""本地向量存储（sqlite-vec）。

持久化长期记忆事件与稳定理解，提供原始候选检索接口。
Pipeline 逻辑（融合/rerank/recency）由 retrieval.py 负责。
"""

from __future__ import annotations

import os
import json
import asyncio
import sqlite3
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, NamedTuple

import aiosqlite

from repository.log_config.memory import memory_logger
from repository.llm.embedding import (
    embed_async,
)
from models import EpisodeMemory
from models.dates import canonical_cn_date
from repository.config import character_path, get_agent_names

_CN_DATE_RE = re.compile(r"^\d{1,2}月\d{1,2}日$")


class _PreparedEpisode(NamedTuple):
    episode: EpisodeMemory
    memory_owner: str
    date: str
    content_hash: str
    recalled_at: str
    embed_text: str


# ----------------------------- 配置与常量 -----------------------------

__all__ = [
    "VectorStore",
    "vector_store",
    "vector_db_path",
]


def vector_db_path() -> str:
    """当前用户向量库路径：data/runtime/{user_id}/vectors.sqlite。"""
    from repository.config import runtime_dir
    return str(runtime_dir() / "vectors.sqlite")


# FTS 只保留中英文和数字，其他标点交给 jieba 切分后丢弃
_FTS_ALLOWED_TOKEN_RE = re.compile(r"[0-9a-z㐀-䶿一-鿿豈-﫿]+")


def _get_jieba():
    try:
        import jieba
    except ModuleNotFoundError as exc:
        raise RuntimeError("未安装 jieba，无法执行中文 FTS 分词；请先同步项目依赖") from exc

    try:
        jieba.setLogLevel(logging.ERROR)
    except Exception:
        pass
    return jieba


def _segment_fts_terms(text: str) -> list[str]:
    """使用 jieba 预分词，输出可直接写入/查询 FTS5 的 token 列表。"""
    if not text or not text.strip():
        return []

    jieba = _get_jieba()
    terms: list[str] = []
    for raw_token in jieba.cut(text, cut_all=False):
        token = raw_token.strip().lower()
        if not token:
            continue
        cleaned = "".join(_FTS_ALLOWED_TOKEN_RE.findall(token))
        if cleaned:
            terms.append(cleaned)
    return terms


def _tokenize_for_fts(text: str) -> str:
    """用 jieba 预分词后按空格拼接，交给 unicode61 按 token 建索引。"""
    return " ".join(_segment_fts_terms(text))


def _build_fts_match_query(text: str, max_terms: int = 32) -> str:
    """将自由文本转换为稳定的 FTS5 MATCH 查询。

    - 去掉 Markdown/FTS 特殊语法，避免 `*`、`:` 等触发解析错误
    - 中文使用 jieba 分词，英文/数字按连续字母数字保留
    - 使用 OR 扩大召回面，由 BM25 自行排序
    """
    terms: list[str] = []
    seen: set[str] = set()
    for token in _segment_fts_terms(text.strip()):
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= max_terms:
            break

    if not terms:
        return ""

    return " OR ".join(f'"{term}"' for term in terms)


# ----------------------------- 表结构常量 -----------------------------

# 长期记忆主表：业务字段与 EpisodeMemory 对齐；其余是存储层必需的功能字段
_EPISODE_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS EpisodeMemory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_owner TEXT NOT NULL,
    game_date TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    time TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 3,
    content_hash TEXT NOT NULL,
    last_recalled_at TEXT
)
"""

_UNDERSTANDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS Understanding (
    db_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    memory_owner TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    linked_episodes TEXT NOT NULL DEFAULT '[]'
)
"""


def _make_embed_text(name: str, keywords: list[str], *content_parts: str) -> str:
    """[名称/标题, 关键词, 正文...] join 为检索索引文本，空段过滤。"""
    parts = [
        name.strip(),
        "、".join(filter(None, (k.strip() for k in keywords))),
        *(part.strip() for part in content_parts),
    ]
    return "\n".join(p for p in parts if p)


class VectorStore:
    """sqlite-vec 本地向量库（EpisodeMemory + Understanding）。

    表结构：
    - EpisodeMemory(id PK, memory_owner, game_date, title, time, location,
                    participants, content, keywords, importance, content_hash,
                    last_recalled_at)
    - EpisodeMemory_vec USING vec0(embedding F32[embedding_dim])  -- rowid = EpisodeMemory.id
    - EpisodeMemory_fts USING fts5(content, keywords)
    - Understanding(db_id PK, id UNIQUE, memory_owner, subject, content, keywords,
                    linked_episodes)
    - Understanding_vec USING vec0(embedding F32[embedding_dim])  -- rowid = Understanding.db_id
    - Understanding_fts USING fts5(subject, keywords)
    """

    def __init__(self):
        # 连接按 user 缓存：user_id -> 独立 sqlite 连接
        self._dbs: dict[str, aiosqlite.Connection] = {}
        self.character_path = character_path
        self._background_tasks: set[asyncio.Task] = set()
        # 连接/建表懒初始化必须串行化；sqlite 扩展加载不是可重复幂等操作。
        self._init_lock: asyncio.Lock | None = None
        self._init_lock_loop: asyncio.AbstractEventLoop | None = None
        # 单连接下显式串行化写事务；按事件循环懒初始化避免跨 loop 复用报错
        self._write_lock: asyncio.Lock | None = None
        self._write_lock_loop: asyncio.AbstractEventLoop | None = None
        # 建表只需执行一次；同样按事件循环绑定，且按 user 隔离
        self._tables_initialized: dict[str, asyncio.AbstractEventLoop] = {}

    def _user_key(self) -> str:
        from repository.user_context import current_user_id
        return current_user_id.get()

    def _user_db_path(self) -> str:
        return vector_db_path()

    def _get_write_lock(self) -> asyncio.Lock:
        """获取当前事件循环绑定的写锁。"""
        loop = asyncio.get_running_loop()
        if self._write_lock is None or self._write_lock_loop is not loop:
            self._write_lock = asyncio.Lock()
            self._write_lock_loop = loop
        return self._write_lock

    def _get_init_lock(self) -> asyncio.Lock:
        """获取当前事件循环绑定的初始化锁。"""
        loop = asyncio.get_running_loop()
        if self._init_lock is None or self._init_lock_loop is not loop:
            self._init_lock = asyncio.Lock()
            self._init_lock_loop = loop
        return self._init_lock

    def _schema_ready_for_loop(self, user_key: str) -> bool:
        loop = asyncio.get_running_loop()
        return (
            self._dbs.get(user_key) is not None
            and self._tables_initialized.get(user_key) is loop
        )

    @staticmethod
    async def _get_existing_vec_dim(db: aiosqlite.Connection) -> int | None:
        """从 sqlite_master 读取已有 EpisodeMemory_vec 的向量维度；表不存在返回 None。"""
        row = await (
            await db.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'EpisodeMemory_vec'"
            )
        ).fetchone()
        if row is None or not row[0]:
            return None
        m = re.search(r"(?:F32|FLOAT32)\s*\[\s*(\d+)\s*\]", row[0], re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _sidecar_recall_by_hash(
        self,
        agent_name: str,
        date: str,
    ) -> dict[str, str]:
        """从旧版 sidecar 文件读取 {content_hash → last_recalled_at}。

        仅用于旧存档 load 后 DB 为空时的降级 fallback。
        """
        state = self._read_memory_recall_state(agent_name)
        result: dict[str, str] = {}
        for entry in state.values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("date", "")).strip() != date:
                continue
            c_hash = str(entry.get("content_hash", "")).strip()
            recalled = str(entry.get("last_recalled_at", "")).strip()
            if c_hash and recalled:
                result[c_hash] = recalled
        return result

    def _read_json_object(self, path: Path) -> dict[str, Any]:
        """读取 sidecar JSON；解析失败返回空对象。"""
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _read_memory_recall_state(self, agent_name: str) -> dict[str, dict[str, Any]]:
        """读取 recall sidecar。"""
        data = self._read_json_object(
            Path(self.character_path(agent_name, ".memory_recall_state.json"))
        )
        return {
            str(key): value
            for key, value in data.items()
            if isinstance(value, dict)
        }

    async def export_recall_state(self, agent_name: str) -> dict[str, dict[str, str]]:
        """从 DB 导出 recall 状态，供存档时写入 sidecar。

        返回格式与 _read_memory_recall_state 兼容：
        {row_id: {"date": ..., "content_hash": ..., "last_recalled_at": ...}}
        """
        db = await self._open_existing_schema()
        if db is None:
            return {}
        rows = await db.execute_fetchall(
            "SELECT id, game_date, content_hash, last_recalled_at "
            "FROM EpisodeMemory WHERE memory_owner = ?",
            (agent_name,),
        )
        state: dict[str, dict[str, str]] = {}
        for row_id, game_date, content_hash, last_recalled_at in rows:
            if row_id is None:
                continue
            state[str(row_id)] = {
                "date": str(game_date or "").strip(),
                "content_hash": str(content_hash or "").strip(),
                "last_recalled_at": str(last_recalled_at or game_date or "").strip(),
            }
        return state

    # ----------------------------- DB 基础 -----------------------------

    async def _load_sqlite_vec(self, conn: aiosqlite.Connection):
        """在当前连接加载 sqlite-vec 扩展；每个连接只应调用一次。"""
        try:
            import sqlite_vec  # type: ignore

            ext_path = sqlite_vec.loadable_path()
            await conn.enable_load_extension(True)
            await conn.execute(f"SELECT load_extension('{ext_path}')")
        except Exception as e:
            raise RuntimeError(f"加载 sqlite-vec 扩展失败，请确认 sqlite_vec 可用: {e}")

    @staticmethod
    def _load_sqlite_vec_sync(conn: sqlite3.Connection):
        """在同步 sqlite3 连接加载 sqlite-vec 扩展；每个连接只应调用一次。"""
        try:
            import sqlite_vec  # type: ignore

            ext_path = sqlite_vec.loadable_path()
            conn.enable_load_extension(True)
            conn.execute(f"SELECT load_extension('{ext_path}')")
        except Exception as e:
            raise RuntimeError(f"加载 sqlite-vec 扩展失败: {e}")

    async def _get_db(self) -> aiosqlite.Connection:
        key = self._user_key()
        conn = self._dbs.get(key)
        if conn is not None:
            return conn
        async with self._get_init_lock():
            return await self._open_db_locked()

    async def _open_db_locked(self) -> aiosqlite.Connection:
        """打开并加载扩展；调用方必须持有初始化锁。"""
        key = self._user_key()
        conn = self._dbs.get(key)
        if conn is not None:
            return conn

        path = self._user_db_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = await aiosqlite.connect(path)
        try:
            await db.execute("PRAGMA journal_mode=WAL;")
            await self._load_sqlite_vec(db)
        except Exception:
            await db.close()
            raise
        self._dbs[key] = db
        return db

    async def close(self) -> None:
        """关闭当前异步 DB 连接。

        aiosqlite 为每个连接维护后台 worker thread。命令行脚本如果不显式关闭，
        主协程结束后进程仍可能等待该线程，表现为脚本打印完成但不退出。
        """
        for conn in self._dbs.values():
            if conn is not None:
                await conn.close()
        self._dbs = {}
        self._init_lock = None
        self._init_lock_loop = None
        self._tables_initialized = {}
        self._write_lock = None
        self._write_lock_loop = None

    async def _vector_tables_exist(self, db: aiosqlite.Connection) -> bool:
        rows = await db.execute_fetchall(
            """
            SELECT name FROM sqlite_master
            WHERE name IN (
                'EpisodeMemory',
                'EpisodeMemory_vec',
                'EpisodeMemory_fts',
                'Understanding',
                'Understanding_vec',
                'Understanding_fts'
            )
            """
        )
        return {
            "EpisodeMemory",
            "EpisodeMemory_vec",
            "EpisodeMemory_fts",
            "Understanding",
            "Understanding_vec",
            "Understanding_fts",
        }.issubset({str(row[0]) for row in rows})

    async def _open_existing_schema(self) -> aiosqlite.Connection | None:
        loop = asyncio.get_running_loop()
        user_key = self._user_key()
        async with self._get_init_lock():
            if self._schema_ready_for_loop(user_key):
                return self._dbs.get(user_key)
            db = await self._open_db_locked()
            if not await self._vector_tables_exist(db):
                return None
            self._tables_initialized[user_key] = loop
            return db

    async def _create_schema(self, embedding_dim: int) -> None:
        loop = asyncio.get_running_loop()
        user_key = self._user_key()
        async with self._get_init_lock():
            if self._schema_ready_for_loop(user_key):
                db = await self._open_db_locked()
                existing_dim = await self._get_existing_vec_dim(db)
                if existing_dim is None or existing_dim == embedding_dim:
                    return
                # 维度变更：重置标志，强制走后面的重建逻辑
                self._tables_initialized.pop(user_key, None)

            if embedding_dim <= 0:
                raise ValueError(f"embedding_dim 必须是正整数: {embedding_dim}")

            db = await self._open_db_locked()

            # ---- 向量维度迁移：换 embedding 模型后自动重建 vec 表 ----
            existing_dim = await self._get_existing_vec_dim(db)
            if existing_dim is not None and existing_dim != embedding_dim:
                memory_logger.warning(
                    "[VectorStore] 向量维度变更 %s → %s，清空向量表（数据将从 jsonl 重建）",
                    existing_dim, embedding_dim,
                )
                await db.execute("BEGIN")
                for tbl in (
                    "EpisodeMemory_vec", "Understanding_vec",
                    "EpisodeMemory_fts", "Understanding_fts",
                ):
                    await db.execute(f"DROP TABLE IF EXISTS {tbl}")
                await db.execute("DELETE FROM EpisodeMemory")
                await db.execute("DELETE FROM Understanding")
                await db.commit()
            # -------------------------------------------------------

            await db.execute(_EPISODE_MEMORY_SCHEMA)
            await db.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS EpisodeMemory_vec USING vec0(
                    embedding F32[{embedding_dim}]
                )
                """
            )
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS EpisodeMemory_fts USING fts5(
                    content, keywords,
                    tokenize='unicode61'
                )
                """
            )
            await db.execute(_UNDERSTANDING_SCHEMA)
            await db.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS Understanding_vec USING vec0(
                    embedding F32[{embedding_dim}]
                )
                """
            )
            # subject+keywords 比 content+keywords 更能精确匹配"这条 understanding 关于什么"，
            # 避免 content 里的叙事词（如「分享」「亲密」）跨关系误召回。
            # IF NOT EXISTS 无法迁移旧 schema，先 DROP 再 CREATE 强制更新。
            await db.execute("DROP TABLE IF EXISTS Understanding_fts")
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS Understanding_fts USING fts5(
                    subject, keywords,
                    tokenize='unicode61'
                )
                """
            )
            await db.commit()
            self._tables_initialized[user_key] = loop

    async def _delete_chunks(
        self, db: aiosqlite.Connection, memory_owner: str, date: str | None = None
    ) -> None:
        """从三张表删除指定 agent（可选指定日期）的记忆块。"""
        if date is not None:
            where, params = "memory_owner = ? AND game_date = ?", (memory_owner, date)
        else:
            where, params = "memory_owner = ?", (memory_owner,)
        del_cursor = await db.execute(f"SELECT id FROM EpisodeMemory WHERE {where}", params)
        for (del_id,) in await del_cursor.fetchall():
            await db.execute("DELETE FROM EpisodeMemory_fts WHERE rowid = ?", (del_id,))
        await db.execute(
            f"DELETE FROM EpisodeMemory_vec WHERE rowid IN (SELECT id FROM EpisodeMemory WHERE {where})",
            params,
        )
        await db.execute(f"DELETE FROM EpisodeMemory WHERE {where}", params)

    @staticmethod
    def _embed_text(episode: EpisodeMemory) -> str:
        """决定拿哪些高密度字段去索引，避免只靠正文丢失检索锚点。"""
        metadata = "；".join(
            f"{label}：{cleaned}"
            for label, value in (
                ("日期", episode.date),
                ("时间", episode.time),
                ("地点", episode.location),
                ("在场", episode.participants),
            )
            if (cleaned := str(value).strip())
        )
        return _make_embed_text(
            episode.title, episode.keywords, metadata, episode.content
        )

    @staticmethod
    def _understanding_embed_text(understanding: object) -> str:
        """subject + keywords + content 作为 embed 文本。"""
        return _make_embed_text(
            getattr(understanding, "subject", ""),
            getattr(understanding, "keywords", []),
            getattr(understanding, "content", ""),
        )

    def _prepare_episode_index(
        self,
        episode: EpisodeMemory,
        sidecar_cache: dict[tuple[str, str], dict[str, str]] | None = None,
    ) -> _PreparedEpisode | None:
        """校验并准备单条 EpisodeMemory 的索引元数据。"""
        memory_owner = episode.memory_owner.strip()
        if not memory_owner:
            memory_logger.warning("[VectorStore] 跳过长期记忆索引: memory_owner 为空")
            return None

        date = canonical_cn_date(episode.date)
        if not date or not _CN_DATE_RE.match(date):
            memory_logger.warning(
                "[VectorStore] 跳过长期记忆索引: memory_owner=%s, date=%s, 日期格式无效",
                memory_owner, episode.date,
            )
            return None

        if not episode.content.strip():
            memory_logger.info(
                "[VectorStore] 跳过长期记忆索引: memory_owner=%s, date=%s, 无有效事件",
                memory_owner, date,
            )
            return None

        content_hash = hashlib.sha1(episode.content.encode("utf-8")).hexdigest()
        cache_key = (memory_owner, date)
        recalled_at = canonical_cn_date(episode.last_recalled_at) or date
        if "last_recalled_at" not in episode.model_fields_set:
            if sidecar_cache is None:
                sidecar_recall = self._sidecar_recall_by_hash(memory_owner, date)
            else:
                if cache_key not in sidecar_cache:
                    sidecar_cache[cache_key] = self._sidecar_recall_by_hash(
                        memory_owner,
                        date,
                    )
                sidecar_recall = sidecar_cache[cache_key]
            recalled_at = sidecar_recall.get(content_hash, recalled_at)
        return _PreparedEpisode(
            episode=episode,
            memory_owner=memory_owner,
            date=date,
            content_hash=content_hash,
            recalled_at=recalled_at,
            embed_text=self._embed_text(episode),
        )

    async def _insert_chunk(
        self,
        db: aiosqlite.Connection,
        episode: EpisodeMemory,
        memory_owner: str,
        date: str,
        content_hash: str,
        recalled_at: str,
        embedding: list[float],
        *,
        embed_text: str,
    ) -> None:
        """将单条 EpisodeMemory 与 embedding 写入三张表，不做删除。"""
        keywords_str = "、".join(episode.keywords)
        cur = await db.execute(
            "INSERT INTO EpisodeMemory("
            "memory_owner, game_date, title, time, location, "
            "participants, content, keywords, importance, content_hash, last_recalled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_owner,
                date,
                episode.title,
                episode.time,
                episode.location,
                episode.participants,
                episode.content,
                keywords_str,
                episode.importance,
                content_hash,
                recalled_at,
            ),
        )
        rowid = int(cur.lastrowid or 0)
        if rowid:
            await db.execute(
                "INSERT OR REPLACE INTO EpisodeMemory_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, self._to_vec_blob(embedding)),
            )
            await db.execute(
                "INSERT INTO EpisodeMemory_fts(rowid, content, keywords) VALUES (?, ?, ?)",
                (
                    rowid,
                    _tokenize_for_fts(embed_text),
                    _tokenize_for_fts(keywords_str),
                ),
            )

    async def _insert_prepared_batch(
        self,
        prepared: list[_PreparedEpisode],
        embeddings: list[list[float]],
    ) -> int:
        """在单个事务内写入一批已完成 embedding 的记忆。"""
        if not prepared:
            return 0
        try:
            embedding_dim = self._embedding_dim(embeddings)
        except ValueError as e:
            memory_logger.error("[VectorStore] embedding 维度无效: %s", e)
            return 0

        db: aiosqlite.Connection | None = None
        try:
            async with self._get_write_lock():
                await self._create_schema(embedding_dim)
                db = await self._get_db()

                await db.execute("BEGIN")
                for item, embedding in zip(prepared, embeddings, strict=True):
                    await self._insert_chunk(
                        db,
                        item.episode,
                        item.memory_owner,
                        item.date,
                        item.content_hash,
                        item.recalled_at,
                        embedding,
                        embed_text=item.embed_text,
                    )
                await db.commit()
                return len(prepared)
        except Exception as e:
            try:
                if db is not None:
                    await db.execute("ROLLBACK")
            except Exception:
                pass
            memory_logger.error(
                "[VectorStore] 批量索引长期记忆失败: count=%s, error=%s",
                len(prepared), e,
            )
            return 0

    async def add_episode(
        self,
        episode: EpisodeMemory,
    ) -> None:
        """将单条 EpisodeMemory 追加写入向量库。"""
        prepared = self._prepare_episode_index(episode)
        if prepared is None:
            return

        # embed_async 是 HTTP 请求，绝不能在写锁或事务内调用
        try:
            embedding = (await embed_async([prepared.embed_text]))[0]
        except Exception as e:
            memory_logger.error(
                "[VectorStore] embedding 计算失败: memory_owner=%s, date=%s, error=%s",
                prepared.memory_owner, prepared.date, e,
            )
            return

        inserted = await self._insert_prepared_batch([prepared], [embedding])
        if inserted:
            memory_logger.debug(
                "[VectorStore] 长期记忆追加索引完成: memory_owner=%s, date=%s",
                prepared.memory_owner, prepared.date,
            )

    async def add_episodes(
        self,
        episodes: list[EpisodeMemory],
        *,
        batch_size: int = 64,
    ) -> int:
        """批量追加长期记忆索引，返回成功写入的记录数。"""
        if batch_size <= 0:
            batch_size = 64

        sidecar_cache: dict[tuple[str, str], dict[str, str]] = {}
        prepared = [
            item
            for episode in episodes
            if (item := self._prepare_episode_index(episode, sidecar_cache)) is not None
        ]
        if not prepared:
            return 0

        inserted_total = 0
        for start in range(0, len(prepared), batch_size):
            batch = prepared[start:start + batch_size]
            try:
                embeddings = await embed_async([item.embed_text for item in batch])
            except Exception as e:
                owners = sorted({item.memory_owner for item in batch})
                memory_logger.error(
                    "[VectorStore] 批量 embedding 计算失败: owners=%s, count=%s, error=%s",
                    owners, len(batch), e,
                )
                continue

            if len(embeddings) != len(batch):
                memory_logger.error(
                    "[VectorStore] 批量 embedding 数量不匹配: expected=%s, got=%s",
                    len(batch), len(embeddings),
                )
                continue

            inserted_total += await self._insert_prepared_batch(batch, embeddings)

        memory_logger.info(
            "[VectorStore] 批量长期记忆索引完成: requested=%s, prepared=%s, inserted=%s",
            len(episodes), len(prepared), inserted_total,
        )
        return inserted_total

    # ----------------------------- Understanding 写入 -----------------------------

    async def _insert_understanding_batch(
        self,
        items: list[tuple[object, list[float]]],
        embedding_dim: int,
    ) -> int:
        """在单个事务内写入一批已完成 embedding 的 Understanding 记录，返回写入条数。

        ROLLBACK 在 write_lock 内执行，不会与并发协程的事务相互干扰。
        """
        async with self._get_write_lock():
            await self._create_schema(embedding_dim)
            db = await self._get_db()
            await db.execute("BEGIN")
            try:
                count = 0
                for u, embedding in items:
                    uid: str = getattr(u, "id", "")
                    subject: str = getattr(u, "subject", "")
                    content: str = getattr(u, "content", "")
                    memory_owner: str = getattr(u, "memory_owner", "")
                    keywords_str = "、".join(getattr(u, "keywords", []))
                    linked_str = json.dumps(getattr(u, "linked_episodes", []), ensure_ascii=False)

                    existing = await (
                        await db.execute("SELECT db_id FROM Understanding WHERE id = ?", (uid,))
                    ).fetchone()
                    if existing:
                        old_db_id: int = existing[0]
                        await db.execute("DELETE FROM Understanding_fts WHERE rowid = ?", (old_db_id,))
                        await db.execute("DELETE FROM Understanding_vec WHERE rowid = ?", (old_db_id,))

                    cur = await db.execute(
                        "INSERT OR REPLACE INTO Understanding"
                        "(id, memory_owner, subject, content, keywords, linked_episodes) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (uid, memory_owner, subject, content, keywords_str, linked_str),
                    )
                    db_id = int(cur.lastrowid or 0)
                    if db_id:
                        await db.execute(
                            "INSERT OR REPLACE INTO Understanding_vec(rowid, embedding) VALUES (?, ?)",
                            (db_id, self._to_vec_blob(embedding)),
                        )
                        await db.execute(
                            "INSERT OR REPLACE INTO Understanding_fts(rowid, subject, keywords) VALUES (?, ?, ?)",
                            (db_id, _tokenize_for_fts(subject), _tokenize_for_fts(keywords_str)),
                        )
                        count += 1
                await db.commit()
                return count
            except Exception:
                try:
                    await db.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    async def add_understanding(self, understanding: object) -> None:
        """将单条 Understanding 写入向量库（已存在时先删后插）。"""
        uid: str = getattr(understanding, "id", "")
        if not uid:
            return
        embed_text = self._understanding_embed_text(understanding)
        if not embed_text.strip():
            return
        try:
            embedding = (await embed_async([embed_text]))[0]
        except Exception as e:
            memory_logger.error("[VectorStore] Understanding embedding 失败: id=%s, error=%s", uid, e)
            return
        try:
            inserted = await self._insert_understanding_batch([(understanding, embedding)], len(embedding))
            if inserted:
                memory_logger.debug(
                    "[VectorStore] Understanding 写入完成: id=%s, owner=%s",
                    uid, getattr(understanding, "memory_owner", ""),
                )
        except Exception as e:
            memory_logger.error("[VectorStore] Understanding 写入失败: id=%s, error=%s", uid, e)

    async def add_understandings(
        self,
        understandings: list[object],
        *,
        batch_size: int = 64,
    ) -> int:
        """批量写入 Understanding 向量库，返回成功写入的记录数。"""
        items: list[tuple[object, str]] = []
        for u in understandings:
            if not getattr(u, "id", ""):
                continue
            text = self._understanding_embed_text(u)
            if text.strip():
                items.append((u, text))
        if not items:
            return 0

        inserted_total = 0
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            try:
                embeddings = await embed_async([text for _, text in batch])
            except Exception as e:
                memory_logger.error(
                    "[VectorStore] Understanding 批量 embedding 失败: count=%s, error=%s",
                    len(batch), e,
                )
                continue
            if len(embeddings) != len(batch):
                memory_logger.error(
                    "[VectorStore] Understanding 批量 embedding 数量不匹配: expected=%s, got=%s",
                    len(batch), len(embeddings),
                )
                continue
            try:
                embedding_dim = self._embedding_dim(embeddings)
                inserted_total += await self._insert_understanding_batch(
                    [(u, emb) for (u, _), emb in zip(batch, embeddings)],
                    embedding_dim,
                )
            except Exception as e:
                memory_logger.error(
                    "[VectorStore] Understanding 批量写入失败: count=%s, error=%s",
                    len(batch), e,
                )

        memory_logger.info(
            "[VectorStore] Understanding 批量索引完成: requested=%s, inserted=%s",
            len(items), inserted_total,
        )
        return inserted_total

    async def update_understanding_links(
        self, understanding_id: str, linked_episodes: list[str]
    ) -> None:
        """仅更新 linked_episodes 字段，不触碰向量或 FTS 索引。"""
        linked_str = json.dumps(linked_episodes, ensure_ascii=False)
        async with self._get_write_lock():
            db = await self._open_existing_schema()
            if db is None:
                return
            try:
                await db.execute("BEGIN")
                await db.execute(
                    "UPDATE Understanding SET linked_episodes = ? WHERE id = ?",
                    (linked_str, understanding_id),
                )
                await db.commit()
            except Exception as e:
                try:
                    await db.execute("ROLLBACK")
                except Exception:
                    pass
                memory_logger.error(
                    "[VectorStore] Understanding links 更新失败: id=%s, error=%s",
                    understanding_id, e,
                )

    async def delete_understanding(self, understanding_id: str) -> None:
        """从三张 Understanding 表删除指定条目。id 不存在时静默返回。"""
        async with self._get_write_lock():
            db = await self._open_existing_schema()
            if db is None:
                return
            try:
                row = await (
                    await db.execute(
                        "SELECT db_id FROM Understanding WHERE id = ?",
                        (understanding_id,),
                    )
                ).fetchone()
                if row is None:
                    return
                db_id: int = row[0]
                await db.execute("BEGIN")
                await db.execute(
                    "DELETE FROM Understanding_fts WHERE rowid = ?", (db_id,)
                )
                await db.execute(
                    "DELETE FROM Understanding_vec WHERE rowid = ?", (db_id,)
                )
                await db.execute(
                    "DELETE FROM Understanding WHERE id = ?", (understanding_id,)
                )
                await db.commit()
            except Exception as e:
                try:
                    await db.execute("ROLLBACK")
                except Exception:
                    pass
                memory_logger.error(
                    "[VectorStore] Understanding 删除失败: id=%s, error=%s",
                    understanding_id, e,
                )

    # ----------------------------- 原始检索 -----------------------------

    def get_vector_candidates(
        self,
        conn: sqlite3.Connection,
        memory_owner: str,
        query_vec: list[float],
        limit: int,
    ) -> list[tuple]:
        """向量近邻检索，返回 (id, content, distance, game_date, last_recalled_at,
        importance, title, time, location, participants) 列表。"""
        candidate_limit = max(limit * 10, 50)
        sql = """
        WITH scope AS (
          SELECT id FROM EpisodeMemory WHERE memory_owner = ?
        ),
        vec_results AS (
          SELECT rowid, distance FROM EpisodeMemory_vec
          WHERE embedding MATCH ?
          LIMIT ?
        )
        SELECT c.id, c.content, v.distance, c.game_date, c.last_recalled_at, c.importance,
               c.title, c.time, c.location, c.participants
        FROM vec_results v
        JOIN scope s ON s.id = v.rowid
        JOIN EpisodeMemory c ON c.id = v.rowid
        ORDER BY v.distance
        LIMIT ?
        """
        return conn.execute(
            sql,
            (memory_owner, self._to_vec_blob(query_vec), candidate_limit, limit),
        ).fetchall()

    @staticmethod
    def get_bm25_candidates(
        conn: sqlite3.Connection,
        memory_owner: str,
        query_text: str,
        limit: int,
    ) -> list[tuple]:
        """BM25 全文检索，返回 (id, content, bm25_rank, game_date, last_recalled_at,
        importance, title, time, location, participants) 列表。

        FTS5 的 rank 值为负数（越小越相关），这里保留原始值供调用方处理。
        """
        fts_query = _build_fts_match_query(query_text)
        if not fts_query:
            return []

        sql = """
        WITH scope AS (
          SELECT id FROM EpisodeMemory WHERE memory_owner = ?
        ),
        fts_hits AS (
          SELECT rowid, bm25(EpisodeMemory_fts, 1.0, 3.0) AS rank
          FROM EpisodeMemory_fts
          WHERE EpisodeMemory_fts MATCH ?
        )
        SELECT c.id, c.content, f.rank, c.game_date, c.last_recalled_at, c.importance,
               c.title, c.time, c.location, c.participants
        FROM fts_hits f
        JOIN scope s ON s.id = f.rowid
        JOIN EpisodeMemory c ON c.id = f.rowid
        ORDER BY f.rank
        LIMIT ?
        """
        try:
            return conn.execute(sql, (memory_owner, fts_query, limit)).fetchall()
        except Exception as e:
            memory_logger.warning("[VectorStore] BM25 检索失败（降级跳过）: %s", e)
            return []

    def get_understanding_vector_candidates(
        self,
        conn: sqlite3.Connection,
        memory_owner: str,
        query_vec: list[float],
        limit: int,
    ) -> list[tuple]:
        """Understanding 向量近邻检索。

        返回 (db_id, id, subject, content, keywords, distance) 列表。
        """
        candidate_limit = max(limit * 10, 50)
        sql = """
        WITH scope AS (
          SELECT db_id FROM Understanding WHERE memory_owner = ?
        ),
        vec_results AS (
          SELECT rowid, distance FROM Understanding_vec
          WHERE embedding MATCH ?
          LIMIT ?
        )
        SELECT u.db_id, u.id, u.subject, u.content, u.keywords, v.distance
        FROM vec_results v
        JOIN scope s ON s.db_id = v.rowid
        JOIN Understanding u ON u.db_id = v.rowid
        ORDER BY v.distance
        LIMIT ?
        """
        return conn.execute(
            sql,
            (memory_owner, self._to_vec_blob(query_vec), candidate_limit, limit),
        ).fetchall()

    @staticmethod
    def get_understanding_bm25_candidates(
        conn: sqlite3.Connection,
        memory_owner: str,
        query_text: str,
        limit: int,
    ) -> list[tuple]:
        """Understanding BM25 检索。

        返回 (db_id, id, subject, content, keywords, bm25_rank) 列表。
        """
        fts_query = _build_fts_match_query(query_text)
        if not fts_query:
            return []
        sql = """
        WITH scope AS (
          SELECT db_id FROM Understanding WHERE memory_owner = ?
        ),
        fts_hits AS (
          SELECT rowid, bm25(Understanding_fts, 1.0, 3.0) AS rank
          FROM Understanding_fts
          WHERE Understanding_fts MATCH ?
        )
        SELECT u.db_id, u.id, u.subject, u.content, u.keywords, f.rank
        FROM fts_hits f
        JOIN scope s ON s.db_id = f.rowid
        JOIN Understanding u ON u.db_id = f.rowid
        ORDER BY f.rank
        LIMIT ?
        """
        try:
            return conn.execute(sql, (memory_owner, fts_query, limit)).fetchall()
        except Exception as e:
            memory_logger.warning(
                "[VectorStore] Understanding BM25 检索失败（降级跳过）: %s", e
            )
            return []

    def update_recall_timestamps(
        self,
        conn: sqlite3.Connection,
        recalled_ids: list[str],
        current_game_date: str,
    ) -> None:
        """更新 DB 中 last_recalled_at。"""
        placeholders = ",".join("?" * len(recalled_ids))
        conn.execute(
            f"UPDATE EpisodeMemory SET last_recalled_at = ? WHERE id IN ({placeholders})",
            (current_game_date, *recalled_ids),
        )

    @staticmethod
    def _embedding_dim(embeddings: list[list[float]]) -> int:
        if not embeddings or not embeddings[0]:
            raise ValueError("接口未返回有效 embedding")
        dim = len(embeddings[0])
        bad_dims = sorted({len(item) for item in embeddings if len(item) != dim})
        if bad_dims:
            raise ValueError(f"同批 embedding 维度不一致: expected={dim}, got={bad_dims}")
        return dim

    @staticmethod
    def _to_vec_blob(vec: list[float]) -> bytes:
        import array
        a = array.array("f", [float(x) for x in vec])
        return a.tobytes()

    # ----------------------------- 删除 -----------------------------

    async def delete(self, memory_owner: str) -> bool:
        """删除指定 agent 的所有记忆索引。"""
        try:
            db = await self._open_existing_schema()
            if db is None:
                return True
            async with self._get_write_lock():
                await self._delete_chunks(db, memory_owner)
                await db.execute(
                    "DELETE FROM Understanding_fts WHERE rowid IN "
                    "(SELECT db_id FROM Understanding WHERE memory_owner = ?)",
                    (memory_owner,),
                )
                await db.execute(
                    "DELETE FROM Understanding_vec WHERE rowid IN "
                    "(SELECT db_id FROM Understanding WHERE memory_owner = ?)",
                    (memory_owner,),
                )
                await db.execute(
                    "DELETE FROM Understanding WHERE memory_owner = ?", (memory_owner,)
                )
                await db.commit()
            memory_logger.info("[VectorStore] 已清空 %s 的记忆索引", memory_owner)
            return True
        except Exception as e:
            memory_logger.error("[VectorStore] 删除 %s 失败: %s", memory_owner, e)
            return False

    async def delete_all_agents(self, agent_names: list[str]) -> dict[str, bool]:
        unique_names = list(dict.fromkeys(agent_names))
        if not unique_names:
            return {}

        all_agents = set(get_agent_names())
        if all_agents and set(unique_names) == all_agents:
            db: aiosqlite.Connection | None = None
            try:
                db = await self._open_existing_schema()
                if db is None:
                    return {name: True for name in unique_names}
                async with self._get_write_lock():
                    await db.execute("BEGIN")
                    await db.execute("DELETE FROM EpisodeMemory_vec")
                    await db.execute("DELETE FROM EpisodeMemory_fts")
                    await db.execute("DELETE FROM EpisodeMemory")
                    await db.execute("DELETE FROM Understanding_vec")
                    await db.execute("DELETE FROM Understanding_fts")
                    await db.execute("DELETE FROM Understanding")
                    await db.commit()
                memory_logger.info("[VectorStore] delete_all_agents 命中全角色，已全量清空记忆索引")
                return {name: True for name in unique_names}
            except Exception as e:
                try:
                    if db is not None:
                        await db.execute("ROLLBACK")
                except Exception:
                    pass
                memory_logger.error(f"[VectorStore] 全量清理失败，回退逐角色删除: {e}")

        return {name: await self.delete(name) for name in unique_names}


# 全局实例
vector_store = VectorStore()
