"""记忆检索查询构造与召回拼装。

检索 pipeline：embedding → vector candidates + BM25 candidates → hybrid fusion
→ (可选 rerank) → recency ranking → recall 状态更新
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

from repository.config import BM25_CANDIDATE_LIMIT, HYBRID_SEARCH_ENABLED, IMPORTANCE_WEIGHT, RELEVANCE_WEIGHT, RECENCY_WEIGHT, RECENCY_HALF_LIFE_DAYS, RECENCY_DATE_WEIGHT, RECENCY_RECALL_WEIGHT, VECTOR_RELEVANCE_WEIGHT, BM25_RELEVANCE_WEIGHT, VECTOR_CANDIDATE_LIMIT, RERANK_TOP_N, EPISODE_SEARCH_LIMIT, UNDERSTANDING_SEARCH_LIMIT, character_path
from repository.log_config.memory import log_retrieval_results, memory_logger
from repository.llm.embedding import embed_sync
from repository.llm.rerank import rerank, RERANK_MODEL
from models.dates import canonical_cn_date, game_day_diff
from repository.status_file import extract_status_field
from repository.vector_store import vector_store, VectorStore, vector_db_path


# ----------------------------- Pipeline 工具函数 -----------------------------

_distance_to_relevance = lambda d: max(0.0, 1.0 - min(float(d), 2.0) / 2.0)


def _sync_tables_exist(conn: sqlite3.Connection, names: set[str]) -> bool:
    if not names:
        return True
    placeholders = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})",
        tuple(names),
    ).fetchall()
    return names.issubset({str(row[0]) for row in rows})


def _decay_from_game_date(current_game_date: str, past_game_date: str, half_life_days: float) -> float:
    """基于游戏内日期做指数衰减；无法解析时返回中性值。"""
    if half_life_days <= 0:
        return 0.5
    days_ago = game_day_diff(current_game_date, past_game_date)
    if days_ago is None:
        return 0.5
    return math.exp(-math.log(2) * days_ago / half_life_days)


def _recency_score(
    current_game_date: str | None,
    event_date: str,
    last_recalled_at: str,
) -> float:
    """计算单条记忆的 recency，按配置权重融合日期与 recall 两个信号。"""
    if not current_game_date:
        return 0.5
    event_score = _decay_from_game_date(current_game_date, event_date, RECENCY_HALF_LIFE_DAYS)
    recall_score = _decay_from_game_date(current_game_date, last_recalled_at or event_date, RECENCY_HALF_LIFE_DAYS)
    weighted_signals: list[tuple[float, float]] = []
    if RECENCY_DATE_WEIGHT > 0:
        weighted_signals.append((event_score, RECENCY_DATE_WEIGHT))
    if RECENCY_RECALL_WEIGHT > 0 and last_recalled_at:
        weighted_signals.append((recall_score, RECENCY_RECALL_WEIGHT))
    if not weighted_signals:
        return 0.5
    total_weight = sum(weight for _, weight in weighted_signals)
    if total_weight <= 0:
        return 0.5
    return sum(score * weight for score, weight in weighted_signals) / total_weight


def _importance_score(raw_importance: Any) -> float:
    """将 EpisodeMemory 的 1-5 重要度归一化到 [0, 1]。"""
    try:
        importance = int(raw_importance)
    except (TypeError, ValueError):
        return 0.5
    bounded = min(max(importance, 1), 5)
    return (bounded - 1) / 4.0


def _load_current_game_date() -> str | None:
    """从 narrator/status.md 读取当前游戏日。"""
    try:
        status = Path(character_path("narrator", "status.md")).read_text(encoding="utf-8")
    except OSError:
        return None
    current_time = extract_status_field(status, "当前时间") if status else ""
    return canonical_cn_date(current_time)


_EPISODE_TUPLE_FIELDS = (
    "title",
    "time",
    "location",
    "participants",
)


def _row_to_doc(row: tuple) -> dict[str, Any]:
    """把 vector/BM25 返回的 10 元素 tuple 解到展示字典。

    tuple layout: (id, content, score, game_date, last_recalled_at, importance,
                   title, time, location, participants)
    其中 score 对 vector 来说是 distance，对 BM25 来说是 bm25_rank。
    """
    doc: dict[str, Any] = {
        "id": str(int(row[0])),
        "content": row[1],
        "date": str(row[3] or ""),
        "last_recalled_at": str(row[4] or ""),
        "importance": int(row[5] or 3),
    }
    for offset, field in enumerate(_EPISODE_TUPLE_FIELDS, start=6):
        doc[field] = str(row[offset] or "") if offset < len(row) else ""
    return doc


def _compute_hybrid_scores(
    candidates: dict[Any, dict[str, Any]],
    use_hybrid: bool,
) -> list[dict[str, Any]]:
    """计算 relevance 分并按降序排列。

    use_hybrid=True: 对 vector_relevance + bm25_raw 做 min-max 归一化后加权融合。
    use_hybrid=False: 直接使用 vector_relevance。
    """
    bm25_min = bm25_max = 0.0
    if use_hybrid:
        bm25_values = [
            float(entry["bm25_raw"])
            for entry in candidates.values()
            if entry.get("bm25_raw") is not None
        ]
        bm25_min = min(bm25_values) if bm25_values else 0.0
        bm25_max = max(bm25_values) if bm25_values else 0.0

    ranked: list[dict[str, Any]] = []
    for entry in candidates.values():
        if use_hybrid:
            bm25_raw = entry.get("bm25_raw")
            if bm25_raw is None:
                bm25_relevance = 0.0
            elif bm25_max > bm25_min:
                bm25_relevance = (float(bm25_raw) - bm25_min) / (bm25_max - bm25_min)
            else:
                bm25_relevance = 1.0
            relevance = (
                VECTOR_RELEVANCE_WEIGHT * float(entry["vector_relevance"])
                + BM25_RELEVANCE_WEIGHT * bm25_relevance
            )
        else:
            relevance = float(entry["vector_relevance"])
        ranked.append({**entry, "relevance": relevance})

    ranked.sort(key=lambda item: item["relevance"], reverse=True)
    return ranked


def hybrid_fusion(
    vec_rows: list[tuple],
    bm25_rows: list[tuple],
) -> list[dict[str, Any]]:
    """按 VECTOR_RELEVANCE_WEIGHT/BM25_RELEVANCE_WEIGHT 组合 relevance。"""
    docs: dict[int, dict[str, Any]] = {}

    for row in vec_rows:
        doc_id = int(row[0])
        doc = _row_to_doc(row)
        doc["vector_relevance"] = _distance_to_relevance(row[2])
        doc["bm25_raw"] = None
        docs[doc_id] = doc

    for row in bm25_rows:
        doc_id = int(row[0])
        if doc_id in docs:
            entry = docs[doc_id]
        else:
            entry = _row_to_doc(row)
            entry["vector_relevance"] = 0.0
            entry["bm25_raw"] = None
            docs[doc_id] = entry
        entry["content"] = row[1]
        entry["bm25_raw"] = abs(float(row[2]))

    results: list[dict[str, Any]] = []
    for entry in _compute_hybrid_scores(docs, use_hybrid=True):
        result_entry = {
            "id": entry["id"],
            "content": entry["content"],
            "relevance": entry["relevance"],
            "date": entry["date"],
            "last_recalled_at": entry["last_recalled_at"],
            "importance": int(entry.get("importance", 3) or 3),
        }
        for field in _EPISODE_TUPLE_FIELDS:
            result_entry[field] = entry.get(field, "")
        results.append(result_entry)

    return results


def apply_recency(
    candidates: list[dict[str, Any]],
    current_game_date: str | None,
) -> list[dict[str, Any]]:
    """叠加 recency / importance 信号，计算最终 score 并排序。"""
    total_weight = RELEVANCE_WEIGHT + RECENCY_WEIGHT + IMPORTANCE_WEIGHT
    relevance_weight = RELEVANCE_WEIGHT / total_weight if total_weight > 0 else 0.7
    recency_weight = RECENCY_WEIGHT / total_weight if total_weight > 0 else 0.3
    importance_weight = IMPORTANCE_WEIGHT / total_weight if total_weight > 0 else 0.0

    ranked: list[dict[str, Any]] = []
    for c in candidates:
        recency = _recency_score(
            current_game_date,
            str(c.get("date", "")),
            str(c.get("last_recalled_at", "")),
        )
        relevance = float(c.get("relevance", 0.0))
        importance = int(c.get("importance", 3) or 3)
        importance_score = _importance_score(importance)
        score = (
            relevance_weight * relevance
            + recency_weight * recency
            + importance_weight * importance_score
        )
        ranked_entry = {
            "id": str(c["id"]),
            "content": str(c["content"]),
            "score": score,
            "relevance": relevance,
            "recency": recency,
            "importance": importance,
            "importance_score": importance_score,
            "date": str(c.get("date", "")),
            "last_recalled_at": str(c.get("last_recalled_at", "")),
        }
        for field in _EPISODE_TUPLE_FIELDS:
            ranked_entry[field] = str(c.get(field, ""))
        ranked.append(ranked_entry)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


# ----------------------------- 主检索入口 -----------------------------

def _try_rerank(
    query: str,
    candidates: list[dict[str, Any]],
    agent_name: str,
    label: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """尝试 rerank，失败时降级保留原结果。返回 (candidates, rerank_applied)。"""
    if not RERANK_MODEL or not candidates:
        return candidates, False
    try:
        return rerank(query, candidates, top_n=RERANK_TOP_N), True
    except Exception as e:
        prefix = f"[Retrieval] {label} " if label else "[Retrieval] "
        memory_logger.warning(f"{prefix}rerank 失败，降级为 fusion 结果: agent={agent_name}, error={e}")
        return candidates, False


def _vec_rows_to_candidates(rows: list[tuple]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        doc = _row_to_doc(row)
        doc["relevance"] = _distance_to_relevance(row[2])
        candidates.append(doc)
    return candidates


def _format_retrieved_memory(item: dict[str, Any]) -> str:
    """将召回结果恢复成带日期上下文的结构化块，交给 LLM 阅读。"""
    content = str(item.get("content", "")).strip()
    if not content:
        return ""
    date = str(item.get("date", "")).strip()
    title = str(item.get("title", "")).strip()
    time = str(item.get("time", "")).strip()
    location = str(item.get("location", "")).strip()
    participants = str(item.get("participants", "")).strip()

    if not any([date, title, time, location, participants]):
        return content

    lines: list[str] = []
    if title:
        lines.append(f"- **标题**：{title}")
    if time:
        lines.append(f"- **时间**：{time}")
    if location:
        lines.append(f"- **地点**：{location}")
    if participants:
        lines.append(f"- **在场**：{participants}")
    lines.append(f"- **内容**：{content}")
    body = "\n".join(lines)

    if not date:
        return body
    return f"## {date}\n{body}"


def _format_retrieved_understanding(item: dict[str, Any]) -> str:
    """将召回的长期判断格式化为 LLM 可读块。"""
    content = str(item.get("content", "")).strip()
    if not content:
        return ""
    subject = str(item.get("subject", "")).strip()
    if subject:
        return f"## {subject}\n{content}"
    return content


def search_memories(
    agent_name: str,
    query: str,
    qvec: list[float] | None = None,
    *,
    bm25_query: str | None = None,
) -> str:
    """执行完整检索 pipeline 并格式化记忆块。

    pipeline 顺序：
    1. embed query → vector 候选（固定 VECTOR_CANDIDATE_LIMIT 条）
    2. hybrid: 若启用则叠加 BM25 候选，按 75/25 权重融合 relevance；
               否则直接用 vector distance 转换 relevance
    3. rerank（可选）: 用 rerank API 分替换 relevance，min-max 归一化到 [0,1]
    4. score: 叠加游戏内时间衰减与 EpisodeMemory 重要度，计算最终 score 并截取 EPISODE_SEARCH_LIMIT 条
    5. 更新命中条目的 last_recalled_at（DB）
    """
    if not query or not query.strip():
        return "（无相关记忆）"
    if not Path(vector_db_path()).exists():
        return "（无相关记忆）"

    # Step 1: 计算查询向量
    try:
        if qvec is None:
            qvec = embed_sync([query])[0]
    except Exception as e:
        memory_logger.error("[Retrieval] 查询嵌入失败: agent=%s, error=%s", agent_name, e)
        return "（无相关记忆）"

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(vector_db_path())
        VectorStore._load_sqlite_vec_sync(conn)
        if not _sync_tables_exist(conn, {"EpisodeMemory", "EpisodeMemory_vec"}):
            return "（无相关记忆）"

        # Step 2: 拉取候选，hybrid 时合并 BM25；否则纯向量
        vec_rows = vector_store.get_vector_candidates(conn, agent_name, qvec, VECTOR_CANDIDATE_LIMIT)
        bm25_rows: list[tuple] = []

        if HYBRID_SEARCH_ENABLED:
            stripped_bm25 = bm25_query.strip() if bm25_query else ""
            lexical_query = stripped_bm25 or query
            bm25_rows = vector_store.get_bm25_candidates(
                conn, agent_name, lexical_query, BM25_CANDIDATE_LIMIT
            )
            if bm25_rows:
                candidates = hybrid_fusion(vec_rows, bm25_rows)
            else:
                candidates = _vec_rows_to_candidates(vec_rows)
        else:
            candidates = _vec_rows_to_candidates(vec_rows)
        candidate_count = len(candidates)
        candidates, rerank_applied = _try_rerank(query, candidates, agent_name)

        # Step 4: recency 加权排序，截取最终返回数
        current_game_date = _load_current_game_date()
        ranked = apply_recency(candidates, current_game_date)[:EPISODE_SEARCH_LIMIT]

        # Step 5: 更新命中条目的 last_recalled_at
        recalled_ids = [r["id"] for r in ranked if r["id"]]
        if recalled_ids and current_game_date:
            try:
                vector_store.update_recall_timestamps(conn, recalled_ids, current_game_date)
                conn.commit()
            except Exception as e:
                memory_logger.warning("[Retrieval] 更新 last_recalled_at 失败: %s", e)

        log_retrieval_results(
            source="episode",
            agent_name=agent_name,
            embedding_query=query,
            bm25_query=lexical_query if HYBRID_SEARCH_ENABLED else None,
            ranked=ranked,
            limit=EPISODE_SEARCH_LIMIT,
            hybrid_enabled=HYBRID_SEARCH_ENABLED,
            vector_candidate_count=len(vec_rows),
            bm25_candidate_count=len(bm25_rows),
            candidate_count=candidate_count,
            rerank_enabled=bool(RERANK_MODEL),
            rerank_applied=rerank_applied,
            rerank_model=RERANK_MODEL,
        )

    except Exception as e:
        memory_logger.error("[Retrieval] 检索失败: agent=%s, error=%s", agent_name, e)
        return "（无相关记忆）"
    finally:
        if conn is not None:
            conn.close()

    memories = [formatted for r in ranked if (formatted := _format_retrieved_memory(r))]
    return "\n\n---\n\n".join(memories) if memories else "（无相关记忆）"


def search_understandings(
    agent_name: str,
    query: str,
    qvec: list[float] | None = None,
    *,
    bm25_query: str | None = None,
) -> str:
    """检索角色长期判断，返回格式化文本。

    Understanding 不做 recency 排序，也不更新 recall 状态；只按相关性取前若干条。
    """
    if not query or not query.strip():
        return ""
    if not Path(vector_db_path()).exists():
        return ""

    try:
        if qvec is None:
            qvec = embed_sync([query])[0]
    except Exception as e:
        memory_logger.error(
            "[Retrieval] Understanding 查询嵌入失败: agent=%s, error=%s",
            agent_name,
            e,
        )
        return ""

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(vector_db_path())
        VectorStore._load_sqlite_vec_sync(conn)
        if not _sync_tables_exist(conn, {"Understanding", "Understanding_vec"}):
            return ""

        vec_rows = vector_store.get_understanding_vector_candidates(
            conn, agent_name, qvec, VECTOR_CANDIDATE_LIMIT
        )

        candidates: dict[str, dict[str, Any]] = {}
        for row in vec_rows:
            uid = str(row[1] or "")
            if not uid:
                continue
            candidates[uid] = {
                "id": uid,
                "subject": str(row[2] or ""),
                "content": str(row[3] or ""),
                "keywords": str(row[4] or ""),
                "vector_relevance": _distance_to_relevance(float(row[5])),
                "bm25_raw": None,
            }

        use_hybrid = False
        bm25_rows: list[tuple] = []
        stripped_bm25 = bm25_query.strip() if bm25_query else ""
        lexical_query = stripped_bm25 or query
        if HYBRID_SEARCH_ENABLED:
            bm25_rows = vector_store.get_understanding_bm25_candidates(
                conn, agent_name, lexical_query, BM25_CANDIDATE_LIMIT
            )
            use_hybrid = bool(bm25_rows)
            for row in bm25_rows:
                uid = str(row[1] or "")
                if not uid:
                    continue
                entry = candidates.setdefault(
                    uid,
                    {
                        "id": uid,
                        "subject": str(row[2] or ""),
                        "content": str(row[3] or ""),
                        "keywords": str(row[4] or ""),
                        "vector_relevance": 0.0,
                        "bm25_raw": None,
                    },
                )
                entry["bm25_raw"] = abs(float(row[5]))

        ranked = _compute_hybrid_scores(candidates, use_hybrid)
        ranked, rerank_applied = _try_rerank(query, ranked, agent_name, label="Understanding")
        top = ranked[:UNDERSTANDING_SEARCH_LIMIT]

        log_retrieval_results(
            source="understanding",
            agent_name=agent_name,
            embedding_query=query,
            bm25_query=lexical_query if HYBRID_SEARCH_ENABLED else None,
            ranked=top,
            limit=UNDERSTANDING_SEARCH_LIMIT,
            hybrid_enabled=HYBRID_SEARCH_ENABLED,
            vector_candidate_count=len(vec_rows),
            bm25_candidate_count=len(bm25_rows),
            candidate_count=len(ranked),
            rerank_enabled=bool(RERANK_MODEL),
            rerank_applied=rerank_applied,
            rerank_model=RERANK_MODEL,
        )

    except Exception as e:
        memory_logger.error(
            "[Retrieval] Understanding 检索失败: agent=%s, error=%s",
            agent_name,
            e,
        )
        return ""
    finally:
        if conn is not None:
            conn.close()

    parts = [
        formatted for item in top if (formatted := _format_retrieved_understanding(item))
    ]
    return "\n\n---\n\n".join(parts) if parts else ""
