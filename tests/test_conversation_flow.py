"""Tests for dialogue orchestration helpers."""

from types import SimpleNamespace

import pytest

import app.conversation_flow as conversation_flow_module
from app.llm_schema import LLMNarratorOutput
from repository.config import AGENT_RUN_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_generate_choices_passes_required_timeout(monkeypatch):
    captured: dict = {}

    async def fake_run_structured_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=["继续问下去"])

    monkeypatch.setattr(conversation_flow_module, "load_conversation_history", lambda turns: [])
    monkeypatch.setattr(
        conversation_flow_module,
        "build_history_transcript",
        lambda _agent_name, _messages: ("", None),
    )
    monkeypatch.setattr(
        conversation_flow_module,
        "get_llm_config",
        lambda: {"model_id": "choices-model"},
    )
    monkeypatch.setattr(conversation_flow_module, "get_choices_agent", lambda: object())
    monkeypatch.setattr(
        conversation_flow_module,
        "run_structured_agent",
        fake_run_structured_agent,
    )

    narrator_output = LLMNarratorOutput(
        targets=["alice"],
        date="4月3日 星期三",
        time="16:10",
        location="走廊",
        present_characters={"北原悠": "门口", "Alice": "窗边"},
        scene_description="场景",
    )
    choices = await conversation_flow_module.generate_choices(narrator_output, [("alice", "回应")])

    assert choices == ["继续问下去"]
    assert captured["timeout_seconds"] == AGENT_RUN_TIMEOUT_SECONDS
