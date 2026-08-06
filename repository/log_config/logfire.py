"""Logfire 初始化与轻量辅助。"""

from __future__ import annotations

import logging
import os
from threading import Lock

_configured = False
_lock = Lock()
_logger = logging.getLogger("logfire")
_APP_LOGGER_NAME = "deeprole"
_LOGFIRE_HANDLER_MARKER = "_deeprole_logfire_handler"


def _attach_logfire_handler(logfire_module: object) -> None:
    """Forward application logs to Logfire through the app logger namespace."""
    handler_cls = getattr(logfire_module, "LogfireLoggingHandler", None)
    if handler_cls is None:
        return

    logger = logging.getLogger(_APP_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if any(getattr(handler, _LOGFIRE_HANDLER_MARKER, False) for handler in logger.handlers):
        return

    try:
        handler = handler_cls(level=logging.INFO)
    except Exception:  # noqa: BLE001
        return

    setattr(handler, _LOGFIRE_HANDLER_MARKER, True)
    logger.addHandler(handler)
    logger.propagate = False


def setup_logfire() -> None:
    """按需初始化 Logfire。

    未安装、未配置或初始化失败时静默跳过，不影响主流程。
    """
    global _configured

    if _configured:
        return

    with _lock:
        if _configured:
            return

        try:
            import logfire
        except ImportError:
            _configured = True
            return

        try:
            environment = os.getenv("LOGFIRE_ENVIRONMENT") or None
            logfire.configure(
                console=False,
                environment=environment,
            )
            logfire.instrument_pydantic_ai(include_content=True, include_binary_content=False)
            _attach_logfire_handler(logfire)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Logfire 初始化失败，已跳过: %s", exc)
        finally:
            _configured = True
