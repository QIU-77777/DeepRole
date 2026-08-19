"""contextvar 用户上下文测试。"""
from repository.user_context import current_user_id, DEFAULT_USER_ID


def test_default_is_default_user():
    assert current_user_id.get() == DEFAULT_USER_ID


def test_set_and_reset_user():
    token_id = "user-abc"
    current_user_id.set(token_id)
    assert current_user_id.get() == token_id
    current_user_id.set(DEFAULT_USER_ID)
    assert current_user_id.get() == DEFAULT_USER_ID
