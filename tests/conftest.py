"""Shared test helpers available to all tests in this directory."""

import pytest

from app.llm_schema import LLMNarratorOutput


@pytest.fixture(autouse=True)
def _isolate_users_db(tmp_path_factory, monkeypatch):
    """把 users.db 指向临时目录，避免测试写入真实 data/users.db。"""
    import repository.user_store

    db = tmp_path_factory.mktemp("users") / "users.db"
    monkeypatch.setattr(repository.user_store, "_USERS_DB", db)


def _narrator_output(**overrides) -> LLMNarratorOutput:
    data = {
        "targets": ["mitsuki"],
        "date": "4月3日 星期三",
        "time": "16:10",
        "location": "走廊",
        "present_characters": {"北原悠": "门口", "美月": "窗边"},
        "scene_description": "走廊里传来广播声。",
        "character_locations": {"北原悠": "走廊", "美月": "走廊"},
        "new_characters": [],
    }
    data.update(overrides)
    return LLMNarratorOutput(**data)
