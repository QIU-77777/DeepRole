"""OpenAI 兼容 Embeddings 客户端。

配置从环境变量读取，兼容 EMBEDDING_MODEL_ID / EMBEDDING_MODEL 两种变量名。
EMBEDDING_DIM 未配置、为空或设为 auto/default 时，不向接口传 dimensions。
"""

from __future__ import annotations

import os

import httpx

from repository.config import EMBEDDING_REQUEST_TIMEOUT_SECONDS


EMBED_MODEL = os.getenv("EMBEDDING_MODEL_ID") or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"
EMBED_API_KEY = os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY", "")
EMBED_API_URL = os.getenv("EMBEDDING_API_URL") or os.getenv("LLM_API_URL", "")


def _parse_embed_dim(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized or normalized in {"auto", "default", "none"}:
        return None
    try:
        dim = int(normalized)
    except ValueError as exc:
        raise ValueError("EMBEDDING_DIM 必须是正整数，或设为 auto/default/空") from exc
    if dim <= 0:
        raise ValueError("EMBEDDING_DIM 必须是正整数")
    return dim


EMBED_DIM = _parse_embed_dim(os.getenv("EMBEDDING_DIM"))


def _validate_embed_config() -> None:
    if not EMBED_API_KEY:
        raise ValueError("EMBEDDING_API_KEY 或 LLM_API_KEY 未配置，无法计算向量")
    if not EMBED_API_URL:
        raise ValueError("EMBEDDING_API_URL 或 LLM_API_URL 未配置，无法计算向量")


def _embedding_payload(texts: list[str]) -> dict[str, object]:
    payload: dict[str, object] = {"model": EMBED_MODEL, "input": texts}
    if EMBED_DIM is not None:
        payload["dimensions"] = EMBED_DIM
    return payload


def _extract_embeddings(data: dict) -> list[list[float]]:
    embeddings = [d["embedding"] for d in data["data"]]
    if EMBED_DIM is not None:
        bad_dims = sorted({len(item) for item in embeddings if len(item) != EMBED_DIM})
        if bad_dims:
            raise ValueError(
                f"Embedding 返回维度与 EMBEDDING_DIM 不一致: expected={EMBED_DIM}, got={bad_dims}"
            )
    return embeddings


async def embed_async(texts: list[str]) -> list[list[float]]:
    """异步计算嵌入。"""
    _validate_embed_config()
    async with httpx.AsyncClient(timeout=EMBEDDING_REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            EMBED_API_URL,
            headers={"Authorization": f"Bearer {EMBED_API_KEY}", "Content-Type": "application/json"},
            json=_embedding_payload(texts),
        )
        resp.raise_for_status()
        return _extract_embeddings(resp.json())


def embed_sync(texts: list[str]) -> list[list[float]]:
    """同步计算嵌入（用于同步检索路径）。"""
    _validate_embed_config()
    resp = httpx.post(
        EMBED_API_URL,
        headers={"Authorization": f"Bearer {EMBED_API_KEY}", "Content-Type": "application/json"},
        json=_embedding_payload(texts),
        timeout=EMBEDDING_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return _extract_embeddings(resp.json())
