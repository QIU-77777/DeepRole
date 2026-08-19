"""users.db token↔user 映射测试。"""
from repository import user_store


def test_create_then_resolve():
    token, user_id = user_store.create_user()
    assert token and user_id
    assert user_store.resolve_user(token) == user_id


def test_resolve_unknown_token_is_none():
    assert user_store.resolve_user("no-such-token") is None


def test_resolve_none_is_none():
    assert user_store.resolve_user(None) is None
    assert user_store.resolve_user("") is None


def test_create_is_unique():
    _, user_id_a = user_store.create_user()
    _, user_id_b = user_store.create_user()
    assert user_id_a != user_id_b
