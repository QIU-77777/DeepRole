"""consolidation flow 后台任务状态按用户隔离。"""

import os
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
os.chdir(project_root)

try:
    from app.consolidation.flow import memory_consolidation_flow
    from repository.user_context import DEFAULT_USER_ID, current_user_id
except ModuleNotFoundError as exc:
    pytest.skip(
        f"skip consolidation isolation tests: missing dependency ({exc})",
        allow_module_level=True,
    )


def test_consolidation_state_scoped_by_user():
    """MemoryConsolidationFlow 的全局状态按用户分区，互不串扰。"""
    memory_consolidation_flow._scheduled_by_user.clear()
    memory_consolidation_flow._active_by_user.clear()
    memory_consolidation_flow._pending_turn_by_user.clear()
    memory_consolidation_flow.last_created_episodes_by_user.clear()

    current_user_id.set("cons-user-a")
    # 模拟 user-a 的整理任务正在运行（占位对象，只校验身份/None）
    memory_consolidation_flow._active_by_user["cons-user-a"] = 1
    memory_consolidation_flow._scheduled_by_user["cons-user-a"] = "fake-task"

    current_user_id.set("cons-user-b")
    # user-b 必须看不到 user-a 的运行状态
    assert memory_consolidation_flow.is_running is False
    assert memory_consolidation_flow._scheduled_task is None
    assert memory_consolidation_flow.last_created_episodes == []

    # user-a 自己仍能看到自己的运行状态
    current_user_id.set("cons-user-a")
    assert memory_consolidation_flow.is_running is True
    assert memory_consolidation_flow._scheduled_task == "fake-task"

    current_user_id.set(DEFAULT_USER_ID)
    for key in ("cons-user-a", "cons-user-b"):
        memory_consolidation_flow._active_by_user.pop(key, None)
        memory_consolidation_flow._scheduled_by_user.pop(key, None)
        memory_consolidation_flow._pending_turn_by_user.pop(key, None)
        memory_consolidation_flow.last_created_episodes_by_user.pop(key, None)
