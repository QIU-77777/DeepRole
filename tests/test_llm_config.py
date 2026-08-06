"""测试 LLM 配置解析。"""

import os
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
os.chdir(project_root)

try:
    import repository.llm.config as llm_config_module
except ModuleNotFoundError as exc:
    pytest.skip(f"skip llm config tests: missing dependency ({exc})", allow_module_level=True)


_CONFIG_KEYS = {"api_url", "api_key", "model_id", "temperature", "provider"}


_LLM_ENV_KEYS = [
    "LLM_MODEL_ID",
    "LLM_API_KEY",
    "LLM_API_URL",
    "LLM_PROVIDER",
]


def _clear_llm_env(monkeypatch):
    for key in _LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_get_llm_config_normalizes_full_chat_completions_url(monkeypatch):
    _clear_llm_env(monkeypatch)

    config = llm_config_module.get_llm_config(
        model_id="gpt-test",
        api_key="main-key",
        api_url="https://example.com/v1/chat/completions",
    )

    assert set(config) == _CONFIG_KEYS
    assert config["model_id"] == "gpt-test"
    assert config["api_key"] == "main-key"
    assert config["api_url"] == "https://example.com/v1"
    assert config["provider"] == "openai"


def test_get_llm_config_reads_model_key_and_url(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_MODEL_ID", "custom-model")
    monkeypatch.setenv("LLM_API_URL", "https://custom.example/v1/chat/completions")

    config = llm_config_module.get_llm_config()

    assert set(config) == _CONFIG_KEYS
    assert config["model_id"] == "custom-model"
    assert config["api_key"] == "main-key"
    assert config["api_url"] == "https://custom.example/v1"
    assert config["provider"] == "openai"


def test_get_llm_config_accepts_temperature_override(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL_ID", "gpt-main")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_API_URL", "https://main.example/v1/chat/completions")

    config = llm_config_module.get_llm_config(temperature=0.42)

    assert set(config) == _CONFIG_KEYS
    assert config["model_id"] == "gpt-main"
    assert config["api_key"] == "main-key"
    assert config["api_url"] == "https://main.example/v1"
    assert config["temperature"] == 0.42
    assert config["provider"] == "openai"


def test_get_llm_config_reads_provider(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL_ID", "gemini-main")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_API_URL", "https://ignored.example/v1")
    monkeypatch.setenv("LLM_PROVIDER", "google")

    config = llm_config_module.get_llm_config()

    assert set(config) == _CONFIG_KEYS
    assert config["provider"] == "google"


def test_get_llm_config_omits_api_url_when_unset(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL_ID", "gpt-main")
    monkeypatch.setenv("LLM_API_KEY", "main-key")

    config = llm_config_module.get_llm_config()

    assert set(config) == _CONFIG_KEYS
    assert config["api_url"] == ""
