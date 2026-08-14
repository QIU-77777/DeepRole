"""测试 narrator 路径的回退和内容清洗（重构后：NarratorService / ConversationService / Repository）。"""

import asyncio
import os
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
os.chdir(project_root)

try:
    import repository.sdk_runner as runner_module
    import app.conversation_service as conversation_service_module
    import app.narrator_service as narrator_service_module
    import repository.log_config.routing as routing_module
    import repository.character_repo as character_repo_module
    import repository.intent_queue as intent_queue_module
    import repository.narrator_repo as narrator_repo_module
    import repository.status_file as status_file_module
    from app.conversation_service import ConversationService
    from app.narrator_service import NarratorService
    from models import Character
    from app.llm_schema import LLMCharacterOutput, LLMNarratorOutput, LLMStateUpdate
    from conftest import _narrator_output
except ModuleNotFoundError as exc:
    pytest.skip(f"skip conversation flow tests: missing dependency ({exc})", allow_module_level=True)


_narrator_service = narrator_service_module.narrator_service
_conversation_service = conversation_service_module.conversation_service


def _stub_player_relation_sync(monkeypatch):
    # _write_scene 在 route() 中写真实文件；测试只关心路由逻辑，统一 stub 掉
    monkeypatch.setattr(NarratorService, "_write_scene", lambda _self, _output: None)


def test_sanitize_narrator_scene_description_truncates_character_dialogue(monkeypatch):
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "read_agent_file", lambda *_args: "# 美月")
    monkeypatch.setattr(narrator_service_module, "get_display_name", lambda *_args: "美月")

    scene = "房间里安静下来。\n美月：这句不该由旁白说。\n她向前走了一步。"
    sanitized = _narrator_service._sanitize_scene_description(scene)

    assert sanitized == "房间里安静下来。"


def test_render_player_relations_computes_from_character_status(monkeypatch):
    """旁白「和玩家的关系」现算：从各角色 status.md 源头汇总，带 ## 段头，不落盘。"""
    import app.prompt_builder as prompt_builder_module

    files = {
        ("role_a", "status.md"): "# A\n\n## 和玩家的关系\n恋人",
        ("role_a", "soul.md"): "# A",
        ("role_b", "status.md"): "# B\n\n## 和玩家的关系\n刚认识\n但有好感",
        ("role_b", "soul.md"): "# B",
        ("role_empty", "status.md"): "# Empty\n\n## 和玩家的关系\n",
        ("role_empty", "soul.md"): "# Empty",
    }
    display_names = {"role_a": "美月", "role_b": "林溪", "role_empty": "空角色"}

    monkeypatch.setattr(
        prompt_builder_module,
        "get_agent_names",
        lambda include_narrator=False: ["role_a", "role_b", "role_empty"],
    )
    monkeypatch.setattr(
        prompt_builder_module, "read_agent_file", lambda agent, filename: files[(agent, filename)]
    )
    monkeypatch.setattr(
        prompt_builder_module,
        "get_display_name",
        lambda agent_name, _soul: display_names[agent_name],
    )

    assert (
        prompt_builder_module.render_player_relations()
        == "## 和玩家的关系\n- 美月：恋人\n- 林溪：刚认识 但有好感"
    )


@pytest.mark.asyncio
async def test_character_run_scans_recent_raw_history_by_turns(monkeypatch):
    history_calls: list[dict] = []

    def fake_load_conversation_history(*, limit=None, turns=None):
        history_calls.append({"limit": limit, "turns": turns})
        return [{"role": "player", "content": "旧消息", "visible_to": ["lilith"]}]

    monkeypatch.setattr(conversation_service_module, "HISTORY_RAW_SCAN_TURNS", 30)
    monkeypatch.setattr(conversation_service_module, "load_conversation_history", fake_load_conversation_history)
    monkeypatch.setattr(
        ConversationService,
        "_build_prompt",
        lambda self, character, user_input, raw_messages, **_kwargs: "prompt",
    )
    monkeypatch.setattr(
        conversation_service_module,
        "get_llm_config",
        lambda: {"model_id": "test-model"},
    )

    async def fake_run_app_agent(*_args, **_kwargs):
        return LLMCharacterOutput(content="回应", memory="")

    def fake_apply_updates(self, name, output):
        return None

    monkeypatch.setattr(conversation_service_module, "run_app_agent", fake_run_app_agent)
    monkeypatch.setattr(ConversationService, "_apply_updates", fake_apply_updates)

    await _conversation_service.run_turn(Character(name="lilith", soul=""), "你好")

    assert history_calls == [{"limit": None, "turns": 30}]


def test_update_status_allow_new_field_appends_missing_section(tmp_path, monkeypatch):
    agent_dir = tmp_path / "narrator"
    agent_dir.mkdir()
    status_path = agent_dir / "status.md"
    status_path.write_text("# 故事状态\n\n## 当前时间\n4月3日\n", encoding="utf-8")

    def fake_character_path(agent_name, *subpaths):
        return str(tmp_path / agent_name / Path(*subpaths))

    monkeypatch.setattr(status_file_module, "character_path", fake_character_path)

    result = status_file_module.update_status_allow_new_field(
        "narrator",
        "和玩家的关系",
        "- 美月：同班同学",
    )

    assert result["operation"] == "replace"
    assert result["target"] == "和玩家的关系"
    assert status_path.read_text(encoding="utf-8") == (
        "# 故事状态\n\n"
        "## 当前时间\n"
        "4月3日\n\n"
        "## 和玩家的关系\n"
        "- 美月：同班同学\n"
    )


@pytest.mark.asyncio
async def test_narrator_route_returns_fallback_on_run_failure(monkeypatch):
    _stub_player_relation_sync(monkeypatch)
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "load_conversation_history", lambda **_kw: [])
    calls = 0

    async def fake_run_narrator(self, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise asyncio.TimeoutError

    monkeypatch.setattr(NarratorService, "_run_narrator", fake_run_narrator)

    output, is_valid = await _narrator_service.route("你好")

    assert calls == 1
    assert output is None
    assert is_valid is False


@pytest.mark.asyncio
async def test_narrator_route_filters_targets_and_sanitizes_scene(monkeypatch):
    _stub_player_relation_sync(monkeypatch)
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "load_conversation_history", lambda **_kw: [])
    monkeypatch.setattr(narrator_service_module, "read_agent_file", lambda *_args: "# 美月")
    monkeypatch.setattr(narrator_service_module, "get_display_name", lambda *_args: "美月")

    async def fake_run_narrator(self, *_args, **_kwargs):
        return _narrator_output(
            targets=["mitsuki", "ghost"],
            scene_description="场景铺垫。\n美月：这句不该由旁白说。",
        )

    monkeypatch.setattr(NarratorService, "_run_narrator", fake_run_narrator)

    output, is_valid = await _narrator_service.route("你好")

    assert output is not None
    assert output.targets == ["mitsuki"]
    assert output.scene_description == "场景铺垫。"
    assert is_valid is True


def test_narrator_route_validation_rejects_unknown_targets():
    output = _narrator_output(targets=["ghost"])

    with pytest.raises(ValueError, match="no valid targets"):
        NarratorService._validate_route_output(output, ["mitsuki"])


def test_narrator_route_validation_rejects_missing_description():
    output = _narrator_output(
        targets=[],
        scene_description="门外有人停下脚步。",
        new_characters=[
            {
                "name_hint": "桥本志津",
                "background_hint": "",
            }
        ],
    )

    with pytest.raises(ValueError, match="missing background_hint"):
        NarratorService._validate_route_output(output, ["mitsuki"])


@pytest.mark.asyncio
async def test_narrator_route_returns_fallback_when_empty_route_is_rejected(monkeypatch):
    _stub_player_relation_sync(monkeypatch)
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "load_conversation_history", lambda **_kw: [])
    calls = 0

    async def fake_run_narrator(self, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("LLMNarratorOutput must include targets or new_characters")

    monkeypatch.setattr(NarratorService, "_run_narrator", fake_run_narrator)

    output, is_valid = await _narrator_service.route("回家睡觉")

    assert calls == 1
    assert output is None
    assert is_valid is False


@pytest.mark.asyncio
async def test_narrator_route_allows_spawn_without_existing_targets(monkeypatch):
    _stub_player_relation_sync(monkeypatch)
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "load_conversation_history", lambda **_kw: [])
    calls = 0

    async def fake_run_narrator(self, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _narrator_output(
            targets=[],
            scene_description="门外有人停下脚步。",
            new_characters=[
                {
                    "name_hint": "桥本志津",
                    "background_hint": "美月的妈妈，温柔而谨慎，常在放学时到校门口等女儿。",
                }
            ],
        )

    monkeypatch.setattr(NarratorService, "_run_narrator", fake_run_narrator)

    output, is_valid = await _narrator_service.route("回家睡觉")

    assert calls == 1
    assert output is not None
    assert output.targets == []
    assert len(output.new_characters) == 1
    assert output.new_characters[0].name_hint == "桥本志津"
    assert output.scene_description == "门外有人停下脚步。"
    assert is_valid is True


@pytest.mark.asyncio
async def test_narrator_route_rejects_scene_without_valid_targets(monkeypatch):
    _stub_player_relation_sync(monkeypatch)
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "load_conversation_history", lambda **_kw: [])

    async def fake_run_narrator(self, *_args, **_kwargs):
        return _narrator_output(targets=["ghost"])

    monkeypatch.setattr(NarratorService, "_run_narrator", fake_run_narrator)

    output, is_valid = await _narrator_service.route("回家睡觉")

    assert output is not None
    assert output.targets == []
    assert output.scene_description == "走廊里传来广播声。"
    assert is_valid is False


def test_state_updater_output_writes_narrator_status_and_events(monkeypatch):
    calls: list[tuple[str, str, str, str | None]] = []

    monkeypatch.setattr(
        narrator_service_module,
        "update_status",
        lambda agent, field, content: calls.append(("status", agent, field, content)) or {},
    )
    monkeypatch.setattr(
        intent_queue_module,
        "remove",
        lambda agent, section, event: calls.append(("triggered", agent, event, section)) or {},
    )
    monkeypatch.setattr(
        intent_queue_module,
        "add",
        lambda agent, section, event: calls.append(("add_event", agent, event, section)) or {},
    )
    monkeypatch.setattr(
        narrator_service_module,
        "update_status_allow_new_field",
        lambda agent, field, content: calls.append(("derived_status", agent, field, content)) or {},
    )

    output = LLMStateUpdate(
        narrative_focus="角色B和玩家在餐厅重逢",
        triggered=["角色B来电"],
        add_event=["【楼下碰面】10月24日 09:30 角色B到达公寓楼下"],
    )

    _narrator_service._apply_state_updates(output)

    # 场景 / 角色位置 / 当前时间 由 _write_scene() 写入，不经过 state_updater
    assert ("status", "narrator", "场景", "餐厅") not in calls
    assert ("status", "narrator", "角色位置", "- 玩家：餐桌旁") not in calls
    assert ("status", "narrator", "当前时间", "10月24日 08:40") not in calls
    assert ("status", "narrator", "叙事焦点", "角色B和玩家在餐厅重逢") in calls
    assert ("derived_status", "narrator", "最近世界事件", "") not in calls
    assert ("triggered", "narrator", "角色B来电", "待触发事件") in calls
    assert (
        "add_event",
        "narrator",
        "【楼下碰面】10月24日 09:30 角色B到达公寓楼下",
        "待触发事件",
    ) in calls


def test_state_updater_updates_recent_world_event_and_marks_schedule(monkeypatch):
    derived_status: list[tuple[str, str, str]] = []
    written: list[tuple] = []
    schedule = {
        "events": [
            {
                "name": "体育祭报名",
                "phase": "准备期",
                "status": "pending",
            }
        ]
    }

    monkeypatch.setattr(
        narrator_service_module,
        "update_status_allow_new_field",
        lambda agent, field, content: derived_status.append((agent, field, content)) or {},
    )
    monkeypatch.setattr(
        narrator_repo_module,
        "read_sidecar_json",
        lambda agent, filename: schedule
        if (agent, filename) == ("narrator", "world_schedule.json")
        else {},
    )
    monkeypatch.setattr(
        narrator_repo_module,
        "write_sidecar_json",
        lambda agent, filename, data: written.append((agent, filename, data)),
    )
    monkeypatch.setattr(intent_queue_module, "add", lambda *_args: None)

    output = LLMStateUpdate(
        recent_world_event="（准备期）体育祭报名周，告示板上贴出了体育祭的海报",
        add_event=["【世界事件：体育祭报名】5月第1周 各班教室。班长宣布报名开始。"],
        triggered_world_events=["体育祭报名"],
    )

    _narrator_service._apply_state_updates(output)

    assert derived_status == [
        (
            "narrator",
            "最近世界事件",
            "（准备期）体育祭报名周，告示板上贴出了体育祭的海报",
        )
    ]
    assert written[0][0:2] == ("narrator", "world_schedule.json")
    assert written[0][2]["events"][0]["status"] == "triggered"


def test_apply_response_updates_logs_structured_file_updates(monkeypatch, tmp_path):
    logs: list[tuple[tuple, dict]] = []

    draft_calls: list[tuple[str, int, str]] = []

    def _fake_append_memory_draft(agent: str, turn: int, text: str) -> None:
        draft_calls.append((agent, turn, text))

    monkeypatch.setattr(character_repo_module, "append_memory_draft", _fake_append_memory_draft)
    monkeypatch.setattr(character_repo_module, "read_turn_counter", lambda: 7)
    monkeypatch.setattr(
        character_repo_module,
        "update_status",
        lambda agent, field, content: {
            "file": "status.md",
            "target": field,
            "operation": "replace",
            "before": "旧场景",
            "after": content,
        },
    )
    monkeypatch.setattr(
        intent_queue_module,
        "remove",
        lambda agent, section, event: {
            "file": "intents.json",
            "target": section,
            "operation": "remove",
            "removed": f"【{event}】去天台",
        },
    )
    monkeypatch.setattr(
        intent_queue_module,
        "add",
        lambda agent, section, event: (
            {
                "file": "intents.json",
                "target": section,
                "operation": "skip",
                "reason": "【重复】已存在，跳过",
            }
            if "重复" in event
            else {
                "file": "intents.json",
                "target": section,
                "operation": "add",
                "added": event,
            }
        ),
    )

    def fake_log_debug(*args, **kwargs):
        logs.append((args, kwargs))

    monkeypatch.setattr(routing_module.routing_logger, "debug", fake_log_debug)

    output = LLMCharacterOutput(
        content="回应",
        memory=(
            "- **时间**：10月24日 上午\n"
            "- **地点**：图书馆\n"
            "- **在场**：我、玩家\n"
            "- **内容**：玩家主动替我解围。"
        ),
        status={"场景": "图书馆二楼靠窗座位"},
        triggered=["去天台"],
        add_event=["【新计划】去图书馆", "【重复】去图书馆"],
    )

    _conversation_service._apply_updates("lilith", output)

    args, kwargs = logs[0]
    assert args == ("[FileUpdate] 文件更新: agent=%s, count=%s", "lilith", 5)
    extra = kwargs["extra"]
    assert extra["event.name"] == "deeprole.routing.file_updates"
    assert extra["file_update.agent"] == "lilith"
    assert extra["file_update.count"] == 5
    assert "file_update.items" not in extra
    assert extra["file_update.updates"] == [
        {
            "file": "memory_draft.jsonl",
            "target": "长期记忆",
            "operation": "append",
            "appended": output.memory,
        },
        {
            "file": "status.md",
            "target": "场景",
            "operation": "replace",
            "before": "旧场景",
            "after": "图书馆二楼靠窗座位",
        },
        {
            "file": "intents.json",
            "target": "打算",
            "operation": "remove",
            "removed": "【去天台】去天台",
        },
        {
            "file": "intents.json",
            "target": "打算",
            "operation": "add",
            "added": "【新计划】去图书馆",
        },
        {
            "file": "intents.json",
            "target": "打算",
            "operation": "skip",
            "reason": "【重复】已存在，跳过",
        },
    ]


@pytest.mark.asyncio
async def test_narrator_update_state_uses_state_updater_agent(monkeypatch):
    captured: dict = {}
    applied: list[tuple] = []
    fake_agent = object()

    def fake_read_agent_file(agent, filename):
        files = {
            ("narrator", "status.md"): "# narrator status\n\n## 场景\n旧场景",
            ("role_b", "status.md"): "# 角色B的状态\n\n## 打算\n- [ ] 【楼下碰面】10月24日 09:30 公寓楼下。去见玩家。",
            ("role_b", "soul.md"): "# 角色B",
        }
        return files.get((agent, filename), "")

    # read_agent_file 在 narrator_service（_format_characters_status）与 narrator_repo
    # （read_status_text）两处使用，需同时打桩；打算改从 intent_queue 读
    monkeypatch.setattr(narrator_service_module, "read_agent_file", fake_read_agent_file)
    monkeypatch.setattr(narrator_repo_module, "read_agent_file", fake_read_agent_file)
    monkeypatch.setattr(
        intent_queue_module,
        "read_queue",
        lambda agent, section: (
            ["【楼下碰面】10月24日 09:30 公寓楼下。去见玩家。"]
            if (agent, section) == ("role_b", "打算")
            else []
        ),
    )
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["role_b"],
    )
    monkeypatch.setattr(narrator_service_module, "get_display_name", lambda *_args: "角色B")
    monkeypatch.setattr(
        narrator_service_module,
        "get_llm_config",
        lambda: {"model_id": "test-model"},
    )
    monkeypatch.setattr(
        narrator_service_module,
        "get_state_updater_agent",
        lambda: fake_agent,
    )
    history_calls: list[dict] = []

    def fake_load_conversation_history(*, limit=None, turns=None):
        history_calls.append({"limit": limit, "turns": turns})
        return [
            {"role": "narrator", "content": "手机在掌心震了一下。"},
            {"role": "player", "content": "送到门口会被家人看到吗？"},
            {"role": "role_b", "content": "应该不会，家里人还没回来。"},
        ]

    monkeypatch.setattr(
        narrator_service_module,
        "load_conversation_history",
        fake_load_conversation_history,
    )

    async def fake_run_structured_agent(**kwargs):
        captured.update(kwargs)
        return LLMStateUpdate(narrative_focus="玩家私下联系角色B")

    def fake_apply_state_updates(self, output):
        applied.append(("narrator", output))

    monkeypatch.setattr(runner_module, "run_structured_agent", fake_run_structured_agent)
    monkeypatch.setattr(NarratorService, "_apply_state_updates", fake_apply_state_updates)

    await _narrator_service.update_state()

    assert captured["agent"] is fake_agent
    assert captured["output_type"] is LLMStateUpdate
    assert captured["usage_agent"] == "state_updater"
    assert history_calls == [{"limit": None, "turns": 5}]
    user_input = captured["user_input"]
    assert user_input.index("<characters_status>") < user_input.index("<current_narrator_status>")
    assert user_input.index("<current_narrator_status>") < user_input.index("<recent_history>")
    assert "玩家: 更早的问题" not in user_input
    assert "旁白: 手机在掌心震了一下。" in user_input
    assert "玩家: 送到门口会被家人看到吗？" in user_input
    assert "role_b: 应该不会，家里人还没回来。" in user_input
    assert "【role_b / 角色B】" in user_input
    assert "【楼下碰面】10月24日 09:30 公寓楼下。去见玩家。" in user_input
    assert "<character_intentions>" not in user_input
    assert "<player_input>" not in user_input
    assert "<narrator_targets>" not in user_input
    assert "<narrator_content>" not in user_input
    assert "<agent_responses>" not in user_input
    assert "<milestones>" not in user_input
    assert "给角色B发消息" not in user_input
    assert "手机屏幕亮了一下。" not in user_input
    assert "我看到了。" not in user_input
    assert applied[0][0] == "narrator"
    assert applied[0][1].narrative_focus == "玩家私下联系角色B"
