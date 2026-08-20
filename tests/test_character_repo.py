"""character_repo soul 缓存按用户隔离。"""
from repository.character_repo import CharacterRepository
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_soul_cache_scoped_by_user(tmp_path, monkeypatch):
    repo = CharacterRepository()
    current_user_id.set("cr-user-a")
    cache_key_a = id(repo._user_cache())
    current_user_id.set("cr-user-b")
    cache_key_b = id(repo._user_cache())
    current_user_id.set(DEFAULT_USER_ID)
    assert cache_key_a != cache_key_b


def test_soul_cache_data_scoped_by_user():
    repo = CharacterRepository()
    current_user_id.set("cr-user-a")
    repo._user_cache()["mitsuki"] = "soul-a"
    current_user_id.set("cr-user-b")
    assert repo._user_cache().get("mitsuki") is None
    current_user_id.set(DEFAULT_USER_ID)
