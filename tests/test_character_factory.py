"""测试动态生成新角色：narrator 的 new_characters 过滤 + character_factory bootstrap。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


project_root = Path(__file__).parent.parent
os.chdir(project_root)

try:
    import app.character_factory as character_factory_module
    import app.conversation_flow as conversation_flow_module
    import app.narrator_service as narrator_service_module
    from app.llm_schema import (
        LLMNarratorOutput,
        LLMNewCharacterProfile,
        LLMNewCharacterRequest,
    )
    from app.narrator_service import NarratorService
    from app.character_factory import CreatedCharacterInfo
except ModuleNotFoundError as exc:
    pytest.skip(f"skip character_factory tests: missing dependency ({exc})", allow_module_level=True)


def _narrator_output(**overrides) -> LLMNarratorOutput:
    data = {
        "targets": ["mitsuki"],
        "date": "4月3日 星期三",
        "time": "16:10",
        "location": "走廊",
        "present_characters": {"北原悠": "门口", "美月": "窗边"},
        "scene_description": "场景",
        "new_characters": [],
    }
    data.update(overrides)
    return LLMNarratorOutput(**data)


# ---------------------------------------------------------------------------
# Narrator._filter_new_characters
# ---------------------------------------------------------------------------


def test_filter_new_characters_keeps_valid_specs():
    specs = [
        LLMNewCharacterRequest(
            name_hint="桥本志津",
            background_hint="美月的妈妈，温柔但严厉，常在放学时到校门口等女儿。",
        ),
        LLMNewCharacterRequest(
            name_hint="林清荷",
            background_hint="玩家的表姐，大两岁，在附近工作，偶尔周末来串门。",
        ),
    ]
    kept = NarratorService._filter_new_characters(specs, ["mitsuki"])
    assert [s.name_hint for s in kept] == ["桥本志津", "林清荷"]


def test_filter_new_characters_dedupes_specs():
    specs = [
        LLMNewCharacterRequest(name_hint="桥本志津", background_hint="x"),
        LLMNewCharacterRequest(name_hint="桥本志津", background_hint="x"),
    ]
    kept = NarratorService._filter_new_characters(specs, ["mitsuki"])
    assert len(kept) == 1


def test_filter_new_characters_rejects_empty_description():
    specs = [
        LLMNewCharacterRequest(
            name_hint="桥本志津",
            background_hint="   ",
        ),
    ]
    kept = NarratorService._filter_new_characters(specs, ["mitsuki"])
    assert kept == []


def test_filter_new_characters_dedupes_names():
    specs = [
        LLMNewCharacterRequest(
            name_hint="双胞胎哥哥",
            background_hint="美月的双胞胎哥哥，在外地读大学，偶尔回家。",
        ),
        LLMNewCharacterRequest(
            name_hint="双胞胎哥哥",
            background_hint="美月的双胞胎哥哥，在外地读大学，偶尔回家。",
        ),
    ]
    kept = NarratorService._filter_new_characters(specs, ["mitsuki"])
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# Narrator.route 透传 new_characters，并允许本轮先只申请孵化
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrator_route_passes_new_characters(monkeypatch):
    monkeypatch.setattr(NarratorService, "_write_scene", lambda _self, _output: None)
    monkeypatch.setattr(
        narrator_service_module,
        "get_agent_names",
        lambda include_narrator=False: ["mitsuki"],
    )
    monkeypatch.setattr(narrator_service_module, "load_conversation_history", lambda **_kw: [])
    monkeypatch.setattr(narrator_service_module, "read_agent_file", lambda *_args: "# soul")
    monkeypatch.setattr(narrator_service_module, "get_display_name", lambda *_args: "美月")

    async def fake_run_narrator(self, *_args, **_kwargs):
        return _narrator_output(
            targets=[],
            new_characters=[
                LLMNewCharacterRequest(
                    name_hint="桥本志津",
                    background_hint="美月的妈妈，温柔而谨慎，常在放学时到校门口等女儿。",
                )
            ],
        )

    monkeypatch.setattr(NarratorService, "_run_narrator", fake_run_narrator)

    output, is_valid = await narrator_service_module.narrator_service.route("来一个妈妈")

    assert output is not None
    assert output.targets == []
    assert [s.name_hint for s in output.new_characters] == ["桥本志津"]
    assert is_valid is True


# ---------------------------------------------------------------------------
# character_factory._validate_spec
# ---------------------------------------------------------------------------


@pytest.fixture
def character_dir(tmp_path: Path, monkeypatch):
    from repository import config as shared_config

    monkeypatch.setattr(shared_config, "CHARACTERS_DIR", tmp_path)
    monkeypatch.setattr(character_factory_module, "CHARACTERS_DIR", tmp_path)
    return tmp_path


def _seed(root: Path, name: str, soul: str = "", status: str = "") -> None:
    agent = root / name
    agent.mkdir(parents=True, exist_ok=True)
    if soul:
        (agent / "soul.md").write_text(soul, encoding="utf-8")
    if status:
        (agent / "status.md").write_text(status, encoding="utf-8")


def test_validate_spec_accepts_valid_anchor(character_dir):
    _seed(character_dir, "mitsuki", soul="# 美月")
    spec = LLMNewCharacterRequest(
        name_hint="桥本志津",
        background_hint="美月的妈妈，温柔而谨慎，常在放学时到校门口等女儿。",
    )
    assert character_factory_module._validate_spec(spec) is None


def test_validate_spec_rejects_empty_description(character_dir):
    _seed(character_dir, "mitsuki")
    spec = LLMNewCharacterRequest(
        name_hint="路人",
        background_hint="   ",
    )
    assert character_factory_module._validate_spec(spec) is not None


def test_validate_spec_allows_player_anchor(character_dir):
    spec = LLMNewCharacterRequest(
        name_hint="林清荷",
        background_hint="玩家的表姐，大两岁，在附近工作，偶尔来串门。",
    )
    assert character_factory_module._validate_spec(spec) is None


def test_validate_creation_character_id_rejects_duplicate_name(character_dir):
    _seed(character_dir, "mitsuki")
    _seed(character_dir, "dup")
    assert character_factory_module._validate_creation_character_id("dup") is not None


def test_validate_creation_character_id_rejects_reserved_name(character_dir):
    _seed(character_dir, "mitsuki")
    assert character_factory_module._validate_creation_character_id("player") is not None


def test_validate_creation_character_id_accepts_ascii_name(character_dir):
    _seed(character_dir, "mitsuki")
    assert character_factory_module._validate_creation_character_id("mitsukimom") is None


@pytest.mark.parametrize("character_id", ["MitsukiMom", "mitsuki2", "mitsuki_mom", "美月妈妈"])
def test_new_character_creation_rejects_invalid_character_id_format(character_id: str):
    with pytest.raises(ValueError, match="lowercase ASCII letters"):
        LLMNewCharacterProfile(
            character_id=character_id,
            display_name="桥本志津",
            identity="美月的妈妈，来学校接她放学的家长。",
            goal="你想守住女儿——不抢她的光，但也不想从她的生活里淡出。",
            dynamic="你牵挂着女儿的健康。\n\n每次去学校都忍不住多问几句。",
            behavior=["被女儿嫌弃时，先退一步再绕回来"],
            voice=["美月，你脸色怎么这么差？"],
            initial_status={},
        )


def test_new_character_creation_normalizes_identity_to_single_line():
    creation = LLMNewCharacterProfile(
        character_id="mitsukimom",
        display_name="桥本志津",
        identity="美月的妈妈，\n来学校接她放学的家长。",
        goal="你想守住女儿——不抢她的光，但也不想从她的生活里淡出。",
        dynamic="你牵挂着女儿的健康。\n\n每次去学校都忍不住多问几句。",
        behavior=["被女儿嫌弃时，先退一步再绕回来"],
        voice=["美月，你脸色怎么这么差？"],
        initial_status={},
    )
    assert creation.identity == "美月的妈妈， 来学校接她放学的家长。"


def test_new_character_creation_rejects_blank_goal():
    with pytest.raises(ValueError, match="field cannot be empty"):
        LLMNewCharacterProfile(
            character_id="mitsukimom",
            display_name="桥本志津",
            identity="美月的妈妈，来学校接她放学的家长。",
            goal="   ",
            dynamic="你牵挂着女儿的健康。\n\n每次去学校都忍不住多问几句。",
            behavior=["被女儿嫌弃时，先退一步再绕回来"],
            voice=["美月，你脸色怎么这么差？"],
            initial_status={},
        )


def test_build_factory_user_message_omits_empty_optional_fields(character_dir):
    _seed(character_dir, "mitsuki", soul="# 美月\n", status="## 当前位置\n教室\n")
    _seed(
        character_dir,
        "narrator",
        status="## 当前时间\n4月3日 星期一 8:23\n\n## 场景\n教室\n",
    )

    message = character_factory_module._build_factory_user_message(
        LLMNewCharacterRequest(
            background_hint="美月的妈妈，温柔而谨慎，常在放学时到校门口等女儿。",
        ),
    )

    assert "character_id:" not in message
    assert "name_hint:" not in message
    assert "initial_location:" not in message
    assert "background_hint: 美月的妈妈" in message
    assert "scene_characters" not in message


# ---------------------------------------------------------------------------
# create_character 端到端：mock LLM，确认写入目录结构
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_character_bootstraps_all_files(character_dir, monkeypatch):
    _seed(
        character_dir,
        "mitsuki",
        soul="# 美月\n",
    )
    _seed(
        character_dir,
        "narrator",
        status="## 当前时间\n4月3日 星期一 8:23\n\n## 场景\n教室\n\n## 角色位置\n- 美月：教室\n",
    )

    async def fake_run_structured_agent(**_kwargs):
        return LLMNewCharacterProfile(
            character_id="mitsukimom",
            display_name="桥本志津",
            identity="美月的妈妈，来学校接她放学的家长。",
            goal=(
                "你想一直留在女儿能找到你的位置——她不说累，你就装没看见；"
                "她一松口，你就第一个在。你怕的是有一天她连找你都懒了。"
            ),
            habits=[
                "放学时间：提前到校门口等，手里拎着便当袋，看到美月出来才松口气。",
                "被美月嫌弃时先笑一下退一步，过会儿再绕回来。",
            ],
            reactions=[
                "只要美月脸色不对就忍不住多问一句，问完又怕自己越界。",
                "见到和女儿走近的人时，先礼貌打量，再私下仔细留意这人靠不靠谱。",
            ],
            voice=[
                "美月，今天累不累？妈妈路过顺便来看看你。",
                "吃口东西再走，就一口。",
                "你别硬撑。撑不住的时候要跟妈妈说。",
            ],
            initial_status={
                "身份": "全职主妇",
                "心境": "挂念美月",
                "和玩家的关系": "听说过",
                "在意的事": "女儿练习太累",
                "打算": "- [ ] 【等美月】在教室外等她下课",
            },
        )

    monkeypatch.setattr(
        character_factory_module,
        "run_structured_agent",
        fake_run_structured_agent,
    )
    monkeypatch.setattr(
        character_factory_module,
        "get_character_factory_agent",
        lambda: object(),
    )
    monkeypatch.setattr(
        character_factory_module,
        "get_llm_config",
        lambda: {"model_id": "test"},
    )
    monkeypatch.setattr(
        character_factory_module,
        "reload_conversation_agent",
        lambda _name: None,
    )

    spec = LLMNewCharacterRequest(
        background_hint="美月的妈妈，温柔而谨慎，常在放学时到校门口等女儿。",
        initial_location="教室走廊",
    )
    created = await character_factory_module.create_character(spec)
    assert created == CreatedCharacterInfo(
        character_id="mitsukimom",
        display_name="桥本志津",
        identity="美月的妈妈，来学校接她放学的家长。",
    )

    agent_dir = character_dir / "mitsukimom"
    soul = (agent_dir / "soul.md").read_text(encoding="utf-8")
    assert soul.startswith("<role>桥本志津</role>")
    assert "<identity>\n美月的妈妈，来学校接她放学的家长。\n</identity>" in soul
    assert "<goal>" in soul and "</goal>" in soul and "找到你的位置" in soul
    assert "<habits>" in soul and "退一步" in soul
    assert "<reactions>" in soul and "越界" in soul
    assert "<voice>" in soul and "美月，今天累不累？" in soul
    status = (agent_dir / "status.md").read_text(encoding="utf-8")
    assert status.startswith("# 桥本志津 的状态")
    assert "## 当前位置" not in status
    assert "## 打算\n- [ ] 【等美月】" in status

    narrator_status = (character_dir / "narrator" / "status.md").read_text(encoding="utf-8")
    assert "- 美月：教室" in narrator_status
    assert "- 桥本志津：教室走廊" in narrator_status

    assert "## 和玩家的关系\n听说过" in status


@pytest.mark.asyncio
async def test_create_character_validates_before_calling_llm(character_dir, monkeypatch):
    _seed(character_dir, "mitsuki")
    called = False

    async def fake_run_structured_agent(**_kwargs):
        nonlocal called
        called = True
        return LLMNewCharacterProfile(
            character_id="x",
            display_name="x",
            identity="x",
            goal="x",
            dynamic="x",
            behavior=["x"],
            voice=["x"],
            initial_status={},
        )

    monkeypatch.setattr(
        character_factory_module,
        "run_structured_agent",
        fake_run_structured_agent,
    )

    spec = LLMNewCharacterRequest(
        background_hint="   ",
    )
    created = await character_factory_module.create_character(spec)
    assert created is None
    assert called is False


@pytest.mark.asyncio
async def test_create_character_rejects_invalid_generated_character_id(character_dir, monkeypatch):
    _seed(character_dir, "mitsuki")

    async def fake_run_structured_agent(**_kwargs):
        return LLMNewCharacterProfile(
            character_id="美月妈妈",
            display_name="桥本志津",
            identity="美月的妈妈。",
            goal="你想照顾好女儿。",
            dynamic="你总会多看女儿一眼。",
            behavior=["见到女儿就会停下脚步"],
            voice=["路上小心。"],
            initial_status={},
        )

    monkeypatch.setattr(character_factory_module, "run_structured_agent", fake_run_structured_agent)
    monkeypatch.setattr(character_factory_module, "get_character_factory_agent", lambda: object())
    monkeypatch.setattr(
        character_factory_module,
        "get_llm_config",
        lambda: {"model_id": "test"},
    )

    spec = LLMNewCharacterRequest(
        background_hint="美月的妈妈，温柔而谨慎，常在放学时到校门口等女儿。",
    )
    created = await character_factory_module.create_character(spec)
    assert created is None


# ---------------------------------------------------------------------------
# conversation_flow.bootstrap_new_characters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_new_characters_keeps_only_targeted_successes(monkeypatch):
    async def fake_create_character(spec):
        if spec.name_hint == "坏角色":
            return None
        character_id = "goodone" if spec.name_hint == "好角色1" else "goodtwo"
        return CreatedCharacterInfo(
            character_id=character_id,
            display_name=spec.name_hint,
            identity=f"{character_id}-identity",
        )

    monkeypatch.setattr(conversation_flow_module, "create_character", fake_create_character)

    specs = [
        LLMNewCharacterRequest(name_hint="好角色1", background_hint="x"),
        LLMNewCharacterRequest(name_hint="坏角色", background_hint="x"),
        LLMNewCharacterRequest(name_hint="好角色2", background_hint="x"),
    ]
    targets, created = await conversation_flow_module.bootstrap_new_characters(
        specs, ["mitsuki"]
    )
    assert targets == ["mitsuki", "goodone", "goodtwo"]
    assert [item.character_id for item in created] == ["goodone", "goodtwo"]
    assert [item.identity for item in created] == ["goodone-identity", "goodtwo-identity"]


@pytest.mark.asyncio
async def test_bootstrap_new_characters_auto_targets_created(monkeypatch):
    async def fake_create_character(spec):
        return CreatedCharacterInfo(
            character_id="goodone",
            display_name="Good One",
            identity="新来的角色",
        )

    monkeypatch.setattr(conversation_flow_module, "create_character", fake_create_character)

    specs = [
        LLMNewCharacterRequest(name_hint="Good One", background_hint="x"),
    ]
    targets, created = await conversation_flow_module.bootstrap_new_characters(
        specs, ["mitsuki"]
    )
    assert targets == ["mitsuki", "goodone"]
    assert [item.character_id for item in created] == ["goodone"]


@pytest.mark.asyncio
async def test_bootstrap_new_characters_no_specs_is_noop():
    targets, created = await conversation_flow_module.bootstrap_new_characters([], ["mitsuki"])
    assert targets == ["mitsuki"]
    assert created == []
