"""测试 build_system_prompt 的 prompt 模板选择（建造期 system prompt，归属 agents.factory）。"""

import os
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
os.chdir(project_root)

try:
    import app.agent_factory as factory_module
except ModuleNotFoundError as exc:
    pytest.skip(f"skip prompt builder tests: missing dependency ({exc})", allow_module_level=True)


_TEMPLATE_VARS = "{agent_name} {display_name} {soul} {status_fields}"


def _patch_templates(monkeypatch, character: str, narrator: str) -> None:
    monkeypatch.setattr(factory_module, "CHARACTER", character)
    monkeypatch.setattr(factory_module, "NARRATOR", narrator)


def test_character_system_prompt_reads_character_template(monkeypatch):
    _patch_templates(monkeypatch, "CHARACTER " + _TEMPLATE_VARS, "NARRATOR " + _TEMPLATE_VARS)
    monkeypatch.setattr(factory_module, "get_allowed_fields", lambda agent_name, field: [])

    result = factory_module.build_system_prompt("mitsuki", "# 美月")

    assert result.startswith("CHARACTER mitsuki")


def test_narrator_system_prompt_reads_narrator_template(monkeypatch):
    _patch_templates(monkeypatch, "CHARACTER " + _TEMPLATE_VARS, "NARRATOR {soul}")

    result = factory_module.build_system_prompt("narrator", "# 旁白")

    assert result == "NARRATOR # 旁白"
