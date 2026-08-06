import repository.llm.embedding as embedding_module


def test_embedding_payload_omits_dimensions_by_default(monkeypatch):
    monkeypatch.setattr(embedding_module, "EMBED_DIM", None)

    assert embedding_module._embedding_payload(["hello"]) == {
        "model": embedding_module.EMBED_MODEL,
        "input": ["hello"],
    }


def test_embedding_payload_omits_dimensions_for_auto(monkeypatch):
    assert embedding_module._parse_embed_dim("auto") is None

    monkeypatch.setattr(embedding_module, "EMBED_DIM", None)
    assert "dimensions" not in embedding_module._embedding_payload(["hello"])


def test_embedding_payload_includes_dimensions_for_numeric_value(monkeypatch):
    assert embedding_module._parse_embed_dim("1536") == 1536

    monkeypatch.setattr(embedding_module, "EMBED_DIM", 1536)
    assert embedding_module._embedding_payload(["hello"]) == {
        "model": embedding_module.EMBED_MODEL,
        "input": ["hello"],
        "dimensions": 1536,
    }
