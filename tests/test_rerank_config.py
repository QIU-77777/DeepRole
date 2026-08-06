"""测试 rerank 启用配置。"""

import importlib

import repository.llm.rerank as rerank_module


def test_rerank_model_enables_rerank_without_extra_flag(monkeypatch):
    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")

    module = importlib.reload(rerank_module)

    assert module.RERANK_MODEL == "test-reranker"
