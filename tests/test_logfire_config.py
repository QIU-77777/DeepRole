from __future__ import annotations

import logging
import sys
import types

import repository.log_config.logfire as logfire_config


class FakeLogfireLoggingHandler(logging.Handler):
    def __init__(self, level: int | str = logging.NOTSET) -> None:
        super().__init__(level=level)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _fake_logfire_module() -> types.ModuleType:
    module = types.ModuleType("logfire")
    module.configured_kwargs = []
    module.instrumented_kwargs = []
    module.LogfireLoggingHandler = FakeLogfireLoggingHandler

    def configure(**kwargs: object) -> None:
        module.configured_kwargs.append(kwargs)

    def instrument_pydantic_ai(**kwargs: object) -> None:
        module.instrumented_kwargs.append(kwargs)

    module.configure = configure
    module.instrument_pydantic_ai = instrument_pydantic_ai
    return module


def test_setup_logfire_attaches_handler_to_deeprole_parent_logger(monkeypatch) -> None:
    app_logger = logging.getLogger("deeprole")
    monkeypatch.setattr(app_logger, "handlers", [])
    monkeypatch.setattr(app_logger, "level", logging.NOTSET)
    monkeypatch.setattr(app_logger, "propagate", True)
    monkeypatch.setattr(logfire_config, "_configured", False)
    monkeypatch.delenv("LOGFIRE_ENVIRONMENT", raising=False)

    fake_logfire = _fake_logfire_module()
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire)

    logfire_config.setup_logfire()

    assert fake_logfire.configured_kwargs == [{"console": False, "environment": None}]
    assert fake_logfire.instrumented_kwargs == [
        {"include_content": True, "include_binary_content": False}
    ]
    assert len(app_logger.handlers) == 1
    assert isinstance(app_logger.handlers[0], FakeLogfireLoggingHandler)
    assert app_logger.handlers[0].level == logging.INFO
    assert getattr(app_logger.handlers[0], logfire_config._LOGFIRE_HANDLER_MARKER)
    assert app_logger.level == logging.INFO
    assert app_logger.propagate is False


def test_setup_logfire_is_idempotent(monkeypatch) -> None:
    app_logger = logging.getLogger("deeprole")
    monkeypatch.setattr(app_logger, "handlers", [])
    monkeypatch.setattr(app_logger, "level", logging.NOTSET)
    monkeypatch.setattr(app_logger, "propagate", True)
    monkeypatch.setattr(logfire_config, "_configured", False)

    fake_logfire = _fake_logfire_module()
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire)

    logfire_config.setup_logfire()
    logfire_config.setup_logfire()

    assert len(app_logger.handlers) == 1
    assert len(fake_logfire.configured_kwargs) == 1
    assert len(fake_logfire.instrumented_kwargs) == 1


def test_child_logger_propagates_to_deeprole_parent_handler(monkeypatch) -> None:
    app_logger = logging.getLogger("deeprole")
    routing_logger = logging.getLogger("deeprole.routing")
    monkeypatch.setattr(app_logger, "handlers", [])
    monkeypatch.setattr(app_logger, "level", logging.NOTSET)
    monkeypatch.setattr(app_logger, "propagate", True)
    monkeypatch.setattr(routing_logger, "handlers", [])
    monkeypatch.setattr(routing_logger, "level", logging.INFO)
    monkeypatch.setattr(routing_logger, "propagate", True)

    logfire_config._attach_logfire_handler(_fake_logfire_module())

    handler = app_logger.handlers[0]
    routing_logger.info("route event")

    assert isinstance(handler, FakeLogfireLoggingHandler)
    assert [record.getMessage() for record in handler.records] == ["route event"]
