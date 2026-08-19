"""contextvar 用户上下文测试。"""
from pathlib import Path

from repository.config import character_path, characters_dir, runtime_dir
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_default_is_default_user():
    assert current_user_id.get() == DEFAULT_USER_ID


def test_set_and_reset_user():
    token_id = "user-abc"
    current_user_id.set(token_id)
    assert current_user_id.get() == token_id
    current_user_id.set(DEFAULT_USER_ID)
    assert current_user_id.get() == DEFAULT_USER_ID


def test_runtime_dir_scopes_by_user():
    current_user_id.set(DEFAULT_USER_ID)
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
    current_user_id.set(DEFAULT_USER_ID)
    current_user_id.set("user-a")
    path_a = character_path("narrator", "soul.md")
    current_user_id.set("user-b")
    path_b = character_path("narrator", "soul.md")
    current_user_id.set(DEFAULT_USER_ID)
    assert path_a != path_b
    assert "user-a" in path_a and "user-b" in path_b
    assert path_a.endswith("characters/narrator/soul.md")


def test_characters_dir_scoped():
    current_user_id.set(DEFAULT_USER_ID)
    current_user_id.set("user-c")
    d = characters_dir()
    current_user_id.set(DEFAULT_USER_ID)
    assert d.name == "characters"
    assert "user-c" in str(d)
