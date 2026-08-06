"""Memory business logger and structured memory log events."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

memory_logger = logging.getLogger("deeprole.memory")
memory_logger.setLevel(logging.INFO)

_RETRIEVAL_RESULTS_EVENT = "deeprole.memory.retrieval.results"


def _float_attr(item: Mapping[str, Any], key: str) -> float:
    try:
        return float(item.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _int_attr(item: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(item.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _retrieval_result_items(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": index,
            "id": str(item.get("id", "")),
            "date": str(item.get("date", "")),
            "score": round(_float_attr(item, "score"), 4),
            "relevance": round(_float_attr(item, "relevance"), 4),
            "recency": round(_float_attr(item, "recency"), 4),
            "importance": _int_attr(item, "importance", 3),
            "content": str(item.get("content", "")).strip(),
        }
        for index, item in enumerate(ranked, start=1)
    ]


def log_retrieval_results(
    *,
    source: str,
    agent_name: str,
    embedding_query: str,
    bm25_query: str | None = None,
    ranked: list[dict[str, Any]],
    limit: int,
    hybrid_enabled: bool,
    vector_candidate_count: int,
    bm25_candidate_count: int,
    candidate_count: int,
    rerank_enabled: bool,
    rerank_applied: bool,
    rerank_model: str | None,
) -> None:
    """Log retrieval top hits as one structured event for Logfire."""
    if not memory_logger.isEnabledFor(logging.INFO):
        return
    lexical_query = bm25_query if bm25_query is not None else embedding_query
    results = _retrieval_result_items(ranked)
    result_ids = [item["id"] for item in results]
    memory_logger.info(
        "[Retrieval][%s] Top结果: agent=%s, hits=%s, ids=%s",
        source,
        agent_name,
        len(results),
        ", ".join(result_ids) if result_ids else "无",
        extra={
            "event.name": _RETRIEVAL_RESULTS_EVENT,
            "retrieval.source": source,
            "retrieval.agent": agent_name,
            "retrieval.embedding_query": embedding_query,
            "retrieval.bm25_query": lexical_query,
            "retrieval.limit": limit,
            "retrieval.result_count": len(results),
            "retrieval.result_ids": result_ids,
            "retrieval.results": results,
            "retrieval.hybrid_enabled": hybrid_enabled,
            "retrieval.vector_candidate_count": vector_candidate_count,
            "retrieval.bm25_candidate_count": bm25_candidate_count,
            "retrieval.candidate_count": candidate_count,
            "retrieval.rerank_enabled": rerank_enabled,
            "retrieval.rerank_applied": rerank_applied,
            "retrieval.rerank_model": rerank_model or "",
        },
    )
