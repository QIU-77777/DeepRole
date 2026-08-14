"""测试尾部窗口整理的纯逻辑与写回行为。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# 设置项目根目录
project_root = Path(__file__).parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

try:
    import repository.agent_files as agent_files_module
    import app.consolidation.flow as consolidator_module
    import repository.intent_queue as intent_queue_module
    import repository.memory_store as memory_store_module
    import repository.status_file as status_file_module
    from app.consolidation.flow import MemoryConsolidationFlow
    from app.llm_schema import LLMEpisodeClosure, LLMEpisodeMemory, LLMUnderstandingPatch
    from models import EpisodeMemory, Understanding, UnderstandingHistoryEntry
    from repository.memory_store import (
        append_memory_records,
        read_memory_jsonl,
        read_understandings,
        write_understandings,
    )
except ModuleNotFoundError as exc:
    pytest.skip(f"skip consolidator tests: missing dependency ({exc})", allow_module_level=True)


def _make_character_path(tmp_path: Path):
    def _path(name: str, subpath: str | None = None) -> str:
        base = tmp_path / name
        if subpath:
            return str(base / subpath)
        return str(base)

    return _path


def _event(date: str, slot: str, content: str) -> str:
    return (
        f"- **时间**：{date} {slot}\n"
        "- **地点**：活动室\n"
        "- **在场**：我、他\n"
        f"- **内容**：{content}"
    )


@pytest.mark.asyncio
async def test_schedule_detect_and_consolidate_coalesces_while_running(monkeypatch):
    """后台整理运行中再次调度时，只补跑最新 turn，不并发跑 closure detector。"""
    consolidator = MemoryConsolidationFlow()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []
    active_count = 0
    max_active_count = 0

    async def fake_pipeline(_self, current_turn: int) -> None:
        nonlocal active_count, max_active_count
        calls.append(current_turn)
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        try:
            started.set()
            if current_turn == 10:
                await release.wait()
            await asyncio.sleep(0)
        finally:
            active_count -= 1

    monkeypatch.setattr(
        MemoryConsolidationFlow,
        "_consolidation_pipeline",
        fake_pipeline,
    )

    task = consolidator.schedule_detect_and_consolidate(10)
    await started.wait()

    assert consolidator.schedule_detect_and_consolidate(11) is task
    assert consolidator.schedule_detect_and_consolidate(12) is task
    assert consolidator.is_running is True

    release.set()
    await task

    assert calls == [10, 12]
    assert max_active_count == 1
    assert consolidator.is_running is False


@pytest.mark.asyncio
async def test_scheduled_consolidation_swallows_pipeline_exceptions(monkeypatch):
    """单轮 pipeline 抛异常不应让后台任务以未捕获异常结束，pending turn 仍要补跑。"""
    consolidator = MemoryConsolidationFlow()
    calls: list[int] = []

    async def fake_pipeline(_self, current_turn: int) -> None:
        calls.append(current_turn)
        if current_turn == 1:
            raise RuntimeError("模拟 pipeline 失败")

    monkeypatch.setattr(
        MemoryConsolidationFlow,
        "_consolidation_pipeline",
        fake_pipeline,
    )

    task = consolidator.schedule_detect_and_consolidate(1)
    consolidator._coalesce_pending_turn(2)
    await task

    assert calls == [1, 2]
    assert consolidator.is_running is False
    assert consolidator._scheduled_task is None
    assert task.exception() is None


def test_prepare_slice_returns_none_when_draft_empty(tmp_path, monkeypatch):
    """memory_draft.jsonl 不存在或切片为空时返回 None。"""
    monkeypatch.setattr(memory_store_module, "character_path", _make_character_path(tmp_path))
    consolidator = MemoryConsolidationFlow()

    result = consolidator._prepare_consolidation_slice("linxi", until_turn=5, raw_messages=[])
    assert result is None


def test_prepare_slice_skips_entries_beyond_until_turn(tmp_path, monkeypatch):
    """只取 turn <= until_turn 的 draft 条目，后续 turn 留在 remaining。"""
    monkeypatch.setattr(memory_store_module, "character_path", _make_character_path(tmp_path))

    memory_store_module.append_memory_draft("linxi", 3, "- 第三轮 draft")
    memory_store_module.append_memory_draft("linxi", 5, "- 第五轮 draft")

    raw_messages = [
        {"role": "narrator", "content": "场景 A", "visible_to": ["linxi", "narrator"], "turn": 3},
        {"role": "linxi", "content": "回应 A", "visible_to": ["linxi", "narrator"], "turn": 3},
    ]
    consolidator = MemoryConsolidationFlow()
    result = consolidator._prepare_consolidation_slice(
        "linxi", until_turn=3, raw_messages=raw_messages
    )

    assert result is not None
    taken, remaining, memory_entries, raw_dialogue = result
    assert [r["turn"] for r in taken] == [3]
    assert [r["turn"] for r in remaining] == [5]
    assert "第三轮 draft" in memory_entries
    assert "第五轮 draft" not in memory_entries
    assert "[turn=3]" in raw_dialogue


def test_prepare_slice_returns_none_when_no_entries_within_turn(tmp_path, monkeypatch):
    """Draft 非空但所有条目 turn 都大于 until_turn 时跳过。"""
    monkeypatch.setattr(memory_store_module, "character_path", _make_character_path(tmp_path))
    memory_store_module.append_memory_draft("linxi", 10, "- 未来轮 draft")

    consolidator = MemoryConsolidationFlow()
    result = consolidator._prepare_consolidation_slice(
        "linxi", until_turn=5, raw_messages=[]
    )

    assert result is None


@pytest.mark.asyncio
async def test_detect_closures_does_not_close_latest_open_narrator_turn(monkeypatch):
    """最新 narrator turn 尚未等到下一条旁白，不能被归并。"""
    consolidator = MemoryConsolidationFlow()
    monkeypatch.setattr(
        consolidator_module,
        "get_episode_closure_detector_agent",
        lambda: object(),
    )

    async def fake_run_consolidation_agent(
        self_inner,
        *,
        agent,
        output_type,
        agent_name,
        function_name,
        user,
    ):
        assert output_type is LLMEpisodeClosure
        assert function_name == "episode_closure_detector"
        assert "[turn=4]" in user
        return LLMEpisodeClosure.model_validate(
            {
                "linxi": [
                    {
                        "end_turn": 3,
                        "old_theme": "上一段互动",
                        "new_theme": "当前开放互动",
                        "reason": "turn 4 开始新互动",
                    },
                    {
                        "end_turn": 4,
                        "old_theme": "当前开放互动",
                        "new_theme": "还没有下一条旁白的误判",
                        "reason": "latest turn must remain open",
                    },
                ]
            }
        )

    monkeypatch.setattr(
        MemoryConsolidationFlow,
        "_run_consolidation_agent",
        fake_run_consolidation_agent,
    )

    raw_messages = [
        {"role": "narrator", "content": "旧场景", "turn": 3, "visible_to": ["linxi", "narrator"]},
        {"role": "linxi", "content": "旧回应", "turn": 3, "visible_to": ["linxi", "narrator"]},
        {"role": "narrator", "content": "新场景", "turn": 4, "visible_to": ["linxi", "narrator"]},
        {"role": "linxi", "content": "新回应", "turn": 4, "visible_to": ["linxi", "narrator"]},
    ]

    closures = await consolidator._detect_closures(
        ["linxi"],
        raw_messages,
        earliest_draft_turn=3,
        latest_open_turn=4,
    )

    assert closures == {"linxi": 3}


def test_agent_file_updates_return_structured_json_items(tmp_path, monkeypatch):
    agent_name = "linxi"
    path_helper = _make_character_path(tmp_path)

    monkeypatch.setattr(status_file_module, "character_path", path_helper)

    agent_dir = tmp_path / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status.md").write_text(
        "\n".join(
            [
                "# 我的状态",
                "",
                "## 场景",
                "旧教学楼走廊",
                "",
                "## 打算",
                "- [ ] 【去天台】午休去天台找玩家",
                "",
            ]
        ),
        encoding="utf-8",
    )

    replace_result = status_file_module.update_status(agent_name, "场景", "图书馆二楼靠窗座位")
    assert replace_result == {
        "file": "status.md",
        "target": "场景",
        "operation": "replace",
        "before": "旧教学楼走廊",
        "after": "图书馆二楼靠窗座位",
    }

    # 打算 / 待触发事件已改为 intent_queue 类型化列表（见 storage.intent_queue）
    monkeypatch.setattr(intent_queue_module, "character_path", path_helper)
    intent_queue_module.add(agent_name, "打算", "【去天台】午休去天台找玩家")  # 队列初始

    add_result = intent_queue_module.add(agent_name, "打算", "【新计划】去图书馆")
    assert add_result == {
        "file": "intents.json",
        "target": "打算",
        "operation": "add",
        "added": "【新计划】去图书馆",
    }

    skip_result = intent_queue_module.add(agent_name, "打算", "【新计划】去图书馆")
    assert skip_result == {
        "file": "intents.json",
        "target": "打算",
        "operation": "skip",
        "reason": "【新计划】已存在，跳过",
    }

    remove_result = intent_queue_module.remove(agent_name, "打算", "去天台")
    assert remove_result == {
        "file": "intents.json",
        "target": "打算",
        "operation": "remove",
        "removed": "【去天台】午休去天台找玩家",
    }


@pytest.mark.asyncio
async def test_merge_memory_blocks_uses_episode_memory_generator(monkeypatch):
    consolidator = MemoryConsolidationFlow()
    sentinel_agent = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        consolidator_module,
        "get_episode_memory_generator_agent",
        lambda: sentinel_agent,
    )

    async def fake_run_consolidation_agent(
        self_inner,
        *,
        agent,
        output_type,
        agent_name,
        function_name,
        user,
    ):
        captured["agent"] = agent
        captured["user"] = user
        assert output_type is LLMEpisodeMemory
        assert agent_name == "linxi"
        assert function_name == "episode_memory_generator"
        return LLMEpisodeMemory(
            date="10月19日",
            time="10月19日 晚上",
            location="餐厅",
            participants="我、他",
            keywords=["餐厅", "吃饭", "日常"],
            importance=2,
            content="一起吃饭。",
            title="餐厅晚饭",
        )

    monkeypatch.setattr(
        consolidator_module.MemoryConsolidationFlow,
        "_run_consolidation_agent",
        fake_run_consolidation_agent,
    )

    episode = await consolidator._merge_memory_blocks(
        "linxi",
        "payload",
        "raw 对话原文",
    )

    assert captured["agent"] is sentinel_agent
    assert "<memory_entries>" in captured["user"]
    assert episode.date == "10月19日"
    assert episode.content == "一起吃饭。"
    assert episode.location == "餐厅"
    assert episode.memory_owner == "linxi"
    assert episode.keywords == ["餐厅", "吃饭", "日常"]
    assert episode.importance == 2
    assert episode.title == "餐厅晚饭"
    # raw_dialogue 由流程注入，不由 LLM 输出
    assert episode.raw_dialogue == "raw 对话原文"


def test_apply_understanding_patch_updates_and_adds(monkeypatch):
    class FakeUUID:
        hex = "new-understanding-id"

    monkeypatch.setattr(consolidator_module.uuid, "uuid4", lambda: FakeUUID())

    existing = {
        "u1": Understanding(
            id="u1",
            memory_owner="linxi",
            subject="对玩家的认知",
            keywords=["玩家"],
            content="旧理解。",
            linked_episodes=["e0"],
            history=[
                UnderstandingHistoryEntry(
                    episode_id="e0",
                    date="10月18日",
                    title="旧事件",
                    content="旧理解。",
                )
            ],
        ),
    }
    patch = LLMUnderstandingPatch.model_validate(
        {
            "add": [
                {
                    "subject": "互动模式",
                    "keywords": ["靠近"],
                    "content": "玩家会用行动解释。",
                }
            ],
            "update": {
                " u1 ": {
                    "subject": "",
                    "keywords": ["玩家", "保护"],
                    "content": "玩家在压力下会先确认她是否安全。",
                }
            },
        }
    )

    result = consolidator_module._apply_understanding_patch(
        "linxi",
        existing,
        patch,
        EpisodeMemory(id="e1", date="10月19日", title="餐厅晚饭"),
    )

    assert result.logs == ["UPDATE u1", "ADD new-understanding-id"]
    assert result.updated["u1"].subject == "对玩家的认知"
    assert result.updated["u1"].keywords == ["玩家", "保护"]
    assert result.updated["u1"].linked_episodes == ["e0", "e1"]
    assert [entry.episode_id for entry in result.updated["u1"].history] == ["e0", "e1"]
    assert result.updated["u1"].history[-1].date == "10月19日"
    assert result.updated["u1"].history[-1].title == "餐厅晚饭"
    assert result.updated["u1"].history[-1].content == "玩家在压力下会先确认她是否安全。"
    assert result.updated["new-understanding-id"].memory_owner == "linxi"
    assert result.updated["new-understanding-id"].content == "玩家会用行动解释。"
    assert len(result.updated["new-understanding-id"].history) == 1
    assert result.updated["new-understanding-id"].history[0].episode_id == "e1"
    assert result.updated["new-understanding-id"].history[0].date == "10月19日"
    assert result.updated["new-understanding-id"].history[0].title == "餐厅晚饭"
    assert result.updated["new-understanding-id"].history[0].content == "玩家会用行动解释。"


def test_apply_understanding_patch_does_not_append_history_for_link_only_update():
    existing = {
        "u1": Understanding(
            id="u1",
            memory_owner="linxi",
            subject="对玩家的认知",
            keywords=["玩家"],
            content="玩家会认真履行约定。",
            linked_episodes=["e0"],
        ),
    }
    patch = LLMUnderstandingPatch.model_validate(
        {
            "update": {
                "u1": {
                    "subject": "对玩家的认知",
                }
            }
        }
    )

    result = consolidator_module._apply_understanding_patch(
        "linxi",
        existing,
        patch,
        EpisodeMemory(id="e1", date="10月20日", title="再次确认"),
    )

    assert result.links_only_ids == ["u1"]
    assert result.full_replace_ids == []
    assert result.updated["u1"].subject == "对玩家的认知"
    assert result.updated["u1"].keywords == ["玩家"]
    assert result.updated["u1"].content == "玩家会认真履行约定。"
    assert result.updated["u1"].linked_episodes == ["e0", "e1"]
    assert result.updated["u1"].history == []


def test_render_understandings_uses_temporary_prompt_ids():
    context = consolidator_module._render_understandings_for_prompt(
        {
            "79889b06b1204883b26dcf01b7ff3f21": Understanding(
                id="79889b06b1204883b26dcf01b7ff3f21",
                subject="北原悠的性格特征",
                content="他会记住细节。",
            ),
            "deb8d2bbdda7435199d66d32ed5bd6db": Understanding(
                id="deb8d2bbdda7435199d66d32ed5bd6db",
                subject="我与北原悠的相处方式",
                content="我们相处自然。",
            ),
        }
    )

    assert "[u1] subject='北原悠的性格特征'" in context.text
    assert "[u2] subject='我与北原悠的相处方式'" in context.text
    assert "79889b06" not in context.text
    assert context.id_map == {
        "u1": "79889b06b1204883b26dcf01b7ff3f21",
        "u2": "deb8d2bbdda7435199d66d32ed5bd6db",
    }


def test_resolve_understanding_patch_ids_maps_prompt_ids_to_real_ids():
    existing = {
        "79889b06b1204883b26dcf01b7ff3f21": Understanding(
            id="79889b06b1204883b26dcf01b7ff3f21",
            subject="北原悠的性格特征",
            content="旧理解。",
            linked_episodes=["e0"],
        ),
        "deb8d2bbdda7435199d66d32ed5bd6db": Understanding(
            id="deb8d2bbdda7435199d66d32ed5bd6db",
            subject="我与北原悠的相处方式",
            content="旧理解。",
            linked_episodes=["e0"],
        ),
        "e072064bd6a94edc9070d4eeab29fcb5": Understanding(
            id="e072064bd6a94edc9070d4eeab29fcb5",
            subject="我对北原悠的感情",
            content="旧理解。",
            linked_episodes=["e0"],
        ),
    }
    patch = LLMUnderstandingPatch.model_validate(
        {
            "add": [],
            "update": {
                "u1": {"subject": "北原悠的性格特征"},
                "u2": {"subject": "我与北原悠的相处方式"},
                "u3": {"subject": "我对北原悠的感情"},
            },
        }
    )

    resolved = consolidator_module._resolve_understanding_patch_ids(
        patch,
        existing,
        {
            "u1": "79889b06b1204883b26dcf01b7ff3f21",
            "u2": "deb8d2bbdda7435199d66d32ed5bd6db",
            "u3": "e072064bd6a94edc9070d4eeab29fcb5",
        },
    )
    result = consolidator_module._apply_understanding_patch(
        "mitsuki",
        existing,
        resolved,
        EpisodeMemory(id="episode-id", date="4月18日", title="他记得口味"),
    )

    assert result.logs == [
        "UPDATE 79889b06b1204883b26dcf01b7ff3f21",
        "UPDATE deb8d2bbdda7435199d66d32ed5bd6db",
        "UPDATE e072064bd6a94edc9070d4eeab29fcb5",
    ]
    assert result.links_only_ids == [
        "79889b06b1204883b26dcf01b7ff3f21",
        "deb8d2bbdda7435199d66d32ed5bd6db",
        "e072064bd6a94edc9070d4eeab29fcb5",
    ]
    assert result.updated["79889b06b1204883b26dcf01b7ff3f21"].linked_episodes == [
        "e0",
        "episode-id",
    ]
    assert result.updated["deb8d2bbdda7435199d66d32ed5bd6db"].linked_episodes == [
        "e0",
        "episode-id",
    ]
    assert result.updated["e072064bd6a94edc9070d4eeab29fcb5"].linked_episodes == [
        "e0",
        "episode-id",
    ]


@pytest.mark.asyncio
async def test_apply_pipeline_assigns_episode_id_before_understanding_patch(monkeypatch):
    class FakeUUID:
        hex = "episode-id"

    consolidator = MemoryConsolidationFlow()
    captured: dict[str, str] = {}

    async def fake_merge(_self, _agent_name, _memory_entries, _raw_dialogue):
        return EpisodeMemory(
            date="10月19日",
            time="10月19日 晚上",
            location="餐厅",
            participants="我、他",
            content="一起吃饭。",
            memory_owner="linxi",
        )

    async def fake_understanding(_self, _agent_name, episode):
        captured["id"] = episode.id

    monkeypatch.setattr(consolidator_module.uuid, "uuid4", lambda: FakeUUID())
    monkeypatch.setattr(MemoryConsolidationFlow, "_merge_memory_blocks", fake_merge)
    monkeypatch.setattr(
        MemoryConsolidationFlow, "_patch_understandings", fake_understanding
    )

    episode, errors = await consolidator._apply_consolidation_pipeline(
        "linxi", "draft", "raw"
    )

    assert errors == []
    assert episode is not None
    assert episode.id == "episode-id"
    assert captured["id"] == "episode-id"


@pytest.mark.asyncio
async def test_patch_understandings_writes_file_and_syncs_vectors(tmp_path, monkeypatch):
    class FakeUUID:
        hex = "new-understanding-id"

    consolidator = MemoryConsolidationFlow()
    agent_name = "linxi"
    path_helper = _make_character_path(tmp_path)
    sentinel_agent = object()

    monkeypatch.setattr(consolidator_module.uuid, "uuid4", lambda: FakeUUID())
    monkeypatch.setattr(memory_store_module, "character_path", path_helper)
    monkeypatch.setattr(agent_files_module, "character_path", path_helper)
    monkeypatch.setattr(
        consolidator_module,
        "get_understanding_patch_agent",
        lambda: sentinel_agent,
    )

    agent_dir = tmp_path / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "soul.md").write_text("<identity>林溪</identity>", encoding="utf-8")
    write_understandings(
        agent_name,
        {
            "u1": Understanding(
                id="u1",
                memory_owner=agent_name,
                subject="对玩家的认知",
                keywords=["玩家"],
                content="旧理解。",
                linked_episodes=["e0"],
            )
        },
    )

    async def fake_run_consolidation_agent(
        self_inner,
        *,
        agent,
        output_type,
        agent_name,
        function_name,
        user,
    ):
        assert agent is sentinel_agent
        assert output_type is LLMUnderstandingPatch
        assert agent_name == "linxi"
        assert function_name == "understanding_patch"
        assert '"id": "e1"' in user
        assert "[u1] subject='对玩家的认知'" in user
        assert "linked_episodes" not in user
        assert "<profile>" not in user
        return LLMUnderstandingPatch.model_validate(
            {
                "update": {
                    "u1": {
                        "content": "玩家在压力下会先确认她是否安全。",
                    }
                },
                "add": [
                    {
                        "subject": "互动模式",
                        "content": "玩家会用行动解释误会。",
                    }
                ],
            }
        )

    deleted_ids: list[str] = []
    added_ids: list[str] = []

    async def fake_delete_understanding(uid: str) -> None:
        deleted_ids.append(uid)

    async def fake_add_understanding(understanding: Understanding) -> None:
        added_ids.append(understanding.id)

    monkeypatch.setattr(
        MemoryConsolidationFlow,
        "_run_consolidation_agent",
        fake_run_consolidation_agent,
    )
    monkeypatch.setattr(
        consolidator_module.vector_store,
        "delete_understanding",
        fake_delete_understanding,
    )
    monkeypatch.setattr(
        consolidator_module.vector_store,
        "add_understanding",
        fake_add_understanding,
    )

    await consolidator._patch_understandings(
        agent_name,
        EpisodeMemory(
            id="e1",
            date="10月19日",
            time="10月19日 晚上",
            location="餐厅",
            participants="我、他",
            content="他在她紧张时先确认安全。",
            memory_owner=agent_name,
        ),
    )

    updated = read_understandings(agent_name)
    assert updated["u1"].content == "玩家在压力下会先确认她是否安全。"
    assert updated["u1"].linked_episodes == ["e0", "e1"]
    assert len(updated["u1"].history) == 1
    assert updated["u1"].history[0].episode_id == "e1"
    assert updated["u1"].history[0].date == "10月19日"
    assert updated["u1"].history[0].content == "玩家在压力下会先确认她是否安全。"
    assert updated["new-understanding-id"].content == "玩家会用行动解释误会。"
    assert len(updated["new-understanding-id"].history) == 1
    assert updated["new-understanding-id"].history[0].episode_id == "e1"
    assert updated["new-understanding-id"].history[0].date == "10月19日"
    assert updated["new-understanding-id"].history[0].content == "玩家会用行动解释误会。"
    assert deleted_ids == ["u1"]
    assert added_ids == ["u1", "new-understanding-id"]


@pytest.mark.asyncio
async def test_consolidate_agent_merges_draft_into_memory_and_clears_draft(tmp_path, monkeypatch):
    consolidator = MemoryConsolidationFlow()
    agent_name = "linxi"
    path_helper = _make_character_path(tmp_path)

    monkeypatch.setattr(agent_files_module, "character_path", path_helper)
    monkeypatch.setattr(memory_store_module, "character_path", path_helper)
    monkeypatch.setattr(
        consolidator_module,
        "load_conversation_history",
        lambda **_kw: [
            {
                "role": "narrator",
                "content": "旁白：测试场景",
                "visible_to": [agent_name, "narrator"],
                "turn": 4,
            },
            {
                "role": agent_name,
                "content": "角色回应",
                "visible_to": [agent_name, "narrator"],
                "turn": 4,
            },
        ],
    )
    monkeypatch.setattr(consolidator_module, "backup_file", lambda *_args, **_kwargs: None)

    agent_dir = tmp_path / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)

    # 已有一条存量记忆（日期 10月6日 上午），append-only 流程应保留
    seed_record = EpisodeMemory(
        date="10月6日",
        time="10月6日 上午",
        location="活动室",
        participants="我、他",
        keywords=["活动室", "日常"],
        importance=2,
        content="上午稳定内容。",
    )
    append_memory_records(agent_name, [seed_record])

    # draft 写入 memory_draft.jsonl，带 turn 标记
    memory_store_module.append_memory_draft(agent_name, 3, _event("10月6日", "中午", "中午 draft 内容。"))
    memory_store_module.append_memory_draft(agent_name, 4, _event("10月6日", "下午", "下午 draft 内容。"))
    # 模拟还未闭合的下一轮 draft（turn=5），不应被本次归并
    memory_store_module.append_memory_draft(agent_name, 5, _event("10月6日", "傍晚", "傍晚 draft 内容。"))

    rewritten_episode = EpisodeMemory(
        date="10月6日",
        time="10月6日 中午",
        location="活动室",
        participants="我、他",
        keywords=["活动室", "合并"],
        importance=3,
        content="中午下午合并后内容。",
    )

    async def fake_apply_consolidation_pipeline(
        _self,
        _agent_name: str,
        memory_entries: str,
        raw_dialogue: str = "",
    ):
        assert "中午 draft 内容" in memory_entries
        assert "下午 draft 内容" in memory_entries
        assert "傍晚 draft 内容" not in memory_entries
        assert "上午稳定内容" not in memory_entries
        assert "[turn=4]" in raw_dialogue
        return rewritten_episode, []

    monkeypatch.setattr(
        MemoryConsolidationFlow,
        "_apply_consolidation_pipeline",
        fake_apply_consolidation_pipeline,
    )

    vector_calls: list[EpisodeMemory] = []

    async def fake_add(episode: EpisodeMemory) -> None:
        vector_calls.append(episode)

    monkeypatch.setattr(consolidator_module.vector_store, "add_episode", fake_add)

    result = await consolidator.consolidate_agent(agent_name, until_turn=4)

    assert result is not None

    # append-only: 存量 1 条 + 新增 1 条 = 2 条，并按 append 顺序排列
    records = read_memory_jsonl(agent_name)
    assert len(records) == 2
    assert records[0].content == "上午稳定内容。"
    assert records[1].content == "中午下午合并后内容。"

    # 未闭合的 turn=5 draft 条目应保留
    remaining_draft = memory_store_module.read_memory_draft(agent_name)
    assert [r["turn"] for r in remaining_draft] == [5]
    assert "傍晚 draft 内容" in remaining_draft[0]["text"]

    # 向量索引只接收本次 append 的 1 条新记录
    assert len(vector_calls) == 1
    assert vector_calls[0].memory_owner == agent_name
    assert vector_calls[0].content == "中午下午合并后内容。"
    assert isinstance(vector_calls[0], EpisodeMemory)
    assert isinstance(vector_calls[0].keywords, list)
    assert 1 <= vector_calls[0].importance <= 5
