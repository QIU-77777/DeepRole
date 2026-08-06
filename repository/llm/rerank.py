"""Rerank API 客户端。

调用 OpenAI 兼容的 rerank 接口，返回归一化分替换原始 relevance。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from repository.config import RERANK_REQUEST_TIMEOUT_SECONDS


RERANK_MODEL = os.getenv("RERANK_MODEL", "").strip()
RERANK_API_KEY = os.getenv("RERANK_API_KEY") or os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY", "")
RERANK_API_URL = os.getenv("RERANK_API_URL", "")


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    """调用 rerank API，用归一化后的 rerank 分替换 relevance。

    归一化：min-max 到 [0, 1]，与 BM25 处理方式一致。
    """
    documents = [c["content"] for c in candidates]
    resp = httpx.post(
        RERANK_API_URL,
        headers={"Authorization": f"Bearer {RERANK_API_KEY}", "Content-Type": "application/json"},
        json={"model": RERANK_MODEL, "query": query, "documents": documents, "top_n": top_n},
        timeout=RERANK_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    results = resp.json()["results"]
    if not results:
        return []

    scores = [r["relevance_score"] for r in results]
    min_s = min(scores)
    max_s = max(scores)
    normalized = [
        (r["index"], (r["relevance_score"] - min_s) / (max_s - min_s + 1e-9))
        for r in results
    ]
    normalized.sort(key=lambda x: x[1], reverse=True)

    reranked: list[dict[str, Any]] = []
    for idx, norm_score in normalized:
        if idx >= len(candidates):
            continue
        entry = dict(candidates[idx])
        entry["relevance"] = norm_score
        reranked.append(entry)
    return reranked
