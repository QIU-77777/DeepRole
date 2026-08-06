"""Routing business logger."""

import logging

routing_logger = logging.getLogger("deeprole.routing")
routing_logger.setLevel(logging.INFO)

_FILE_UPDATES_EVENT = "deeprole.routing.file_updates"


def log_file_updates(agent_name: str, results: list[dict]) -> None:
    """批量记录一次回合的文件更新结果，供观测使用。

    results 是 storage 层返回的 FileUpdateResult（结构上即 dict），此处按 dict 处理，
    避免 log_config 反向依赖 storage。
    """
    if not results:
        return
    routing_logger.debug(
        "[FileUpdate] 文件更新: agent=%s, count=%s",
        agent_name,
        len(results),
        extra={
            "event.name": _FILE_UPDATES_EVENT,
            "file_update.agent": agent_name,
            "file_update.count": len(results),
            "file_update.updates": results,
        },
    )
