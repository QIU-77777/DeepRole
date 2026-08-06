import importlib

import pytest

from app.llm_schema import LLMUnderstandingEntry
from models import Understanding

migrate = importlib.import_module("scripts.migrate_to_understandings")


class FakeUUID:
    def __init__(self, value: str) -> None:
        self.hex = value


class FakeVectorStore:
    def __init__(self) -> None:
        self.added: list[Understanding] = []

    async def add_understanding(self, understanding: Understanding) -> None:
        self.added.append(understanding)


class FakeAgent:
    """Minimal stand-in for pydantic-ai Agent."""

    def __init__(self, name: str = "", instructions: str = "") -> None:
        self.name = name
        self.instructions = instructions


def _patch_uuid(monkeypatch: pytest.MonkeyPatch, values: list[str]) -> None:
    ids = iter(values)
    monkeypatch.setattr(migrate.uuid, "uuid4", lambda: FakeUUID(next(ids)))


def _mock_llm_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock LLM config and agent construction so tests don't need real API keys."""
    monkeypatch.setattr(
        migrate,
        "get_llm_config",
        lambda temperature=None: {"model_id": "test-model"},
    )
    monkeypatch.setattr(
        migrate,
        "_build_agent",
        lambda name, instructions, config, output_type, max_tokens=None: FakeAgent(
            name=name, instructions=instructions
        ),
    )


# ---------------------------------------------------------------------------
# _build_migration_input
# ---------------------------------------------------------------------------


def test_build_migration_input_includes_all_sources(monkeypatch):
    monkeypatch.setattr(
        migrate,
        "read_agent_file",
        lambda agent_name, filename: {
            "growth.md": "[P001|对玩家：克制关心→主动靠近] [2024-04-25] 此后会更直接地靠近玩家。",
            "user.md": "# 我眼中的玩家\n\n## 对方是什么人\n玩家很敏锐。",
            "relations.md": "## 莉莉丝\n同班同学，会留意玩家的异常。",
        }[filename],
    )

    result = migrate._build_migration_input("mitsuki")

    assert result is not None
    assert "<growth>" in result
    assert "P001|对玩家：克制关心→主动靠近" in result
    assert "<user_profile>" in result
    assert "玩家很敏锐" in result
    assert "<relations>" in result
    assert "莉莉丝" in result


def test_build_migration_input_empty_relations_omitted(monkeypatch):
    monkeypatch.setattr(
        migrate,
        "read_agent_file",
        lambda agent_name, filename: {
            "growth.md": "[P001|对玩家：A→B] 成长内容。",
            "user.md": "玩家档案。",
            "relations.md": "",
        }[filename],
    )

    result = migrate._build_migration_input("mitsuki")

    assert result is not None
    assert "<growth>" in result
    assert "<user_profile>" in result
    assert "<relations>" not in result


def test_build_migration_input_returns_none_when_no_sources(monkeypatch):
    monkeypatch.setattr(
        migrate,
        "read_agent_file",
        lambda agent_name, filename: "",
    )

    result = migrate._build_migration_input("mitsuki")
    assert result is None


# ---------------------------------------------------------------------------
# migrate_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_agent_calls_llm_and_writes(monkeypatch):
    _patch_uuid(monkeypatch, ["u1", "u2"])
    _mock_llm_infra(monkeypatch)
    fake_store = FakeVectorStore()
    written: dict[str, Understanding] = {}

    monkeypatch.setattr(migrate, "vector_store", fake_store)
    monkeypatch.setattr(migrate, "read_understandings", lambda agent_name: {})
    monkeypatch.setattr(
        migrate,
        "_build_migration_input",
        lambda agent_name: "<growth>\n[P001|对玩家：A→B] content\n</growth>",
    )

    async def fake_run_structured_agent(**kwargs):
        return migrate.MigrationOutput(
            entries=[
                LLMUnderstandingEntry(
                    subject="我对玩家的靠近方式",
                    keywords=["玩家", "靠近"],
                    content="我已经不再克制对玩家的关心。",
                ),
                LLMUnderstandingEntry(
                    subject="玩家在压力下的行为",
                    keywords=["玩家", "压力"],
                    content="他在压力下先行动后解释。",
                ),
            ]
        )

    monkeypatch.setattr(migrate, "run_structured_agent", fake_run_structured_agent)
    monkeypatch.setattr(
        migrate,
        "write_understandings",
        lambda agent_name, understandings: written.update(understandings),
    )

    count = await migrate.migrate_agent("mitsuki")

    assert count == 2
    assert list(written) == ["u1", "u2"]
    assert written["u1"].subject == "我对玩家的靠近方式"
    assert written["u2"].subject == "玩家在压力下的行为"
    assert all(u.memory_owner == "mitsuki" for u in written.values())
    assert [u.id for u in fake_store.added] == ["u1", "u2"]


@pytest.mark.asyncio
async def test_migrate_agent_skips_existing_understandings(monkeypatch):
    fake_store = FakeVectorStore()
    llm_called = False

    async def fake_run(**kwargs):
        nonlocal llm_called
        llm_called = True
        return migrate.MigrationOutput(entries=[])

    monkeypatch.setattr(migrate, "vector_store", fake_store)
    monkeypatch.setattr(migrate, "run_structured_agent", fake_run)
    monkeypatch.setattr(
        migrate,
        "read_understandings",
        lambda agent_name: {
            "existing": Understanding(
                id="existing", memory_owner=agent_name, content="已有理解。"
            )
        },
    )

    count = await migrate.migrate_agent("mitsuki")

    assert count == 0
    assert llm_called is False
    assert fake_store.added == []


@pytest.mark.asyncio
async def test_migrate_agent_returns_zero_when_nothing_to_migrate(monkeypatch):
    _mock_llm_infra(monkeypatch)
    monkeypatch.setattr(migrate, "vector_store", FakeVectorStore())
    monkeypatch.setattr(migrate, "read_understandings", lambda agent_name: {})
    monkeypatch.setattr(
        migrate, "_build_migration_input", lambda agent_name: None
    )

    count = await migrate.migrate_agent("mitsuki")
    assert count == 0


@pytest.mark.asyncio
async def test_migrate_agent_returns_zero_on_llm_failure(monkeypatch):
    _mock_llm_infra(monkeypatch)
    monkeypatch.setattr(migrate, "vector_store", FakeVectorStore())
    monkeypatch.setattr(migrate, "read_understandings", lambda agent_name: {})

    def fake_build_input(agent_name: str) -> str | None:
        return "input"

    monkeypatch.setattr(migrate, "_build_migration_input", fake_build_input)

    async def fake_run_structured_agent(**kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(migrate, "run_structured_agent", fake_run_structured_agent)

    count = await migrate.migrate_agent("mitsuki")
    assert count == 0


@pytest.mark.asyncio
async def test_migrate_agent_filters_empty_content_entries(monkeypatch):
    _patch_uuid(monkeypatch, ["u1"])
    _mock_llm_infra(monkeypatch)
    fake_store = FakeVectorStore()
    written: dict[str, Understanding] = {}

    monkeypatch.setattr(migrate, "vector_store", fake_store)
    monkeypatch.setattr(migrate, "read_understandings", lambda agent_name: {})
    monkeypatch.setattr(
        migrate, "_build_migration_input", lambda agent_name: "input"
    )

    async def fake_run_structured_agent(**kwargs):
        return migrate.MigrationOutput(
            entries=[
                LLMUnderstandingEntry(
                    subject="有效条目",
                    keywords=["测试"],
                    content="有实际内容。",
                ),
                LLMUnderstandingEntry(
                    subject="空内容条目",
                    keywords=["空"],
                    content="",
                ),
                LLMUnderstandingEntry(
                    subject="纯空格条目",
                    keywords=["空格"],
                    content="   ",
                ),
            ]
        )

    monkeypatch.setattr(migrate, "run_structured_agent", fake_run_structured_agent)
    monkeypatch.setattr(
        migrate,
        "write_understandings",
        lambda agent_name, understandings: written.update(understandings),
    )

    count = await migrate.migrate_agent("mitsuki")

    assert count == 1
    assert list(written) == ["u1"]
    assert written["u1"].subject == "有效条目"
    assert len(fake_store.added) == 1
