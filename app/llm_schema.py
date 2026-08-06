"""所有 Agent 的 LLM 结构化输出契约（对话 + 记忆整理）。

命名约定：以 ``LLM`` 前缀标识"这是 LLM 的输出规范"——界定哪些字段交给模型填写。
这些契约与 models/ 里的领域实体（EpisodeMemory / Understanding）刻意保持独立、
不互相继承；实体多出的系统字段（id / memory_owner / 时间戳等）绝不暴露给 LLM。
"""

from typing import Annotated

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from repository.config import MAX_CHOICE_CHARS, MAX_EPISODE_KEYWORDS

ChoiceText = Annotated[str, Field(max_length=MAX_CHOICE_CHARS)]


# ---------------------------------------------------------------------------
# 对话类
# ---------------------------------------------------------------------------


class LLMCharacterOutput(BaseModel):
    content: str
    memory: str
    status: dict[str, str] = Field(default_factory=dict)
    triggered: list[str] = Field(default_factory=list)
    add_event: list[str] = Field(default_factory=list)


class LLMNewCharacterRequest(BaseModel):
    name_hint: str = ""
    background_hint: str
    initial_location: str = ""


class LLMNarratorOutput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    targets: list[str]
    date: str
    time: str
    location: str
    present_characters: dict[str, str]
    scene_description: str
    character_locations: dict[str, str] = Field(default_factory=dict)
    new_characters: list[LLMNewCharacterRequest] = Field(default_factory=list)

    @field_validator("targets", mode="before")
    @classmethod
    def _trim_targets(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [target.strip() if isinstance(target, str) else target for target in value]

    @field_validator("present_characters", mode="before")
    @classmethod
    def _clean_present_characters(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        cleaned = {
            str(name).strip(): str(description).strip()
            for name, description in value.items()
            if str(name).strip() and str(description).strip()
        }
        if not cleaned:
            raise ValueError("present_characters cannot be empty")
        return cleaned

    @field_validator("character_locations", mode="before")
    @classmethod
    def _clean_character_locations(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(name).strip(): str(loc).strip()
            for name, loc in value.items()
            if str(name).strip() and str(loc).strip()
        }

    @field_validator("date", "time", "location", "scene_description")
    @classmethod
    def _ensure_scene_field_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("field cannot be empty")
        return value

    @model_validator(mode="after")
    def _require_route_target_or_new_character(self) -> "LLMNarratorOutput":
        if not self.targets and not self.new_characters:
            raise ValueError("LLMNarratorOutput must include targets or new_characters")
        return self


class LLMNewCharacterProfile(BaseModel):
    """character_factory 输出的完整角色骨架。

    character_id 是最终目录名 / agent 标识，display_name 会写入 soul.md / status.md。
    """

    character_id: str = Field(validation_alias=AliasChoices("character_id", "agent_id"))
    display_name: str
    identity: str
    goal: str
    past: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    reactions: list[str] = Field(default_factory=list)
    voice: list[str] = Field(default_factory=list)
    initial_status: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "character_id",
        "display_name",
        "identity",
        "goal",
        mode="before",
    )
    @classmethod
    def _trim_creation_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("past", "habits", "reactions", "voice", mode="before")
    @classmethod
    def _trim_list_items(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [item.strip() if isinstance(item, str) else item for item in value]

    @field_validator("character_id", "display_name", "identity", "goal")
    @classmethod
    def _ensure_creation_fields_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("field cannot be empty")
        return value

    @field_validator("character_id")
    @classmethod
    def _ensure_character_id_is_lower_ascii_letters(cls, value: str) -> str:
        if not value.isascii() or not value.isalpha() or value != value.lower():
            raise ValueError("character_id must contain only lowercase ASCII letters")
        return value

    @field_validator("identity")
    @classmethod
    def _normalize_identity_to_single_line(cls, value: str) -> str:
        return " ".join(value.split())


class LLMStateUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    narrative_focus: str = ""
    recent_world_event: str = ""
    triggered: list[str] = Field(default_factory=list)
    add_event: list[str] = Field(default_factory=list)
    world_schedule_update: str = ""
    triggered_world_events: list[str] = Field(default_factory=list)


class LLMChoices(BaseModel):
    choices: list[ChoiceText]

    @field_validator("choices", mode="before")
    @classmethod
    def _trim_choice_lengths(cls, choices: object) -> object:
        if not isinstance(choices, list):
            return choices
        return [
            choice.strip()[:MAX_CHOICE_CHARS] if isinstance(choice, str) else choice
            for choice in choices
        ]


# ---------------------------------------------------------------------------
# 记忆整理类
# ---------------------------------------------------------------------------


class LLMEpisodeMemory(BaseModel):
    """EpisodeMemoryGenerator 输出的单条长期记忆事件。

    memory_owner 与 raw_dialogue 由整理流程注入，不交给 LLM 判断。
    raw_dialogue 只作为可回溯的源对话 metadata，不参与向量索引。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    date: str
    time: str
    location: str
    participants: str
    keywords: list[str] = Field(default_factory=list)
    importance: int = 3
    content: str
    title: str = ""
    raw_dialogue: str = ""

    @field_validator("keywords", mode="before")
    @classmethod
    def _clean_keywords(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [s for item in value if (s := str(item).strip())][:MAX_EPISODE_KEYWORDS]

    @field_validator("importance", mode="before")
    @classmethod
    def _clamp_importance(cls, value: object) -> int:
        try:
            return max(1, min(5, int(value)))
        except (TypeError, ValueError):
            return 3


class LLMEpisodeClosureBoundary(BaseModel):
    end_turn: int
    old_theme: str = ""
    new_theme: str = ""
    reason: str = ""


class LLMEpisodeClosure(RootModel[dict[str, list[LLMEpisodeClosureBoundary]]]):
    """消费方取每个数组里 end_turn 最大的边界作为本轮可归并的闭合点。"""


class LLMUnderstandingEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    subject: str = ""
    keywords: list[str] = Field(default_factory=list)
    content: str = ""


class LLMUnderstandingPatch(BaseModel):
    add: list[LLMUnderstandingEntry] = Field(default_factory=list)
    update: dict[str, LLMUnderstandingEntry] = Field(default_factory=dict)

    @field_validator("update", mode="before")
    @classmethod
    def _clean_update_ids(cls, value: object) -> object:
        if not isinstance(value, dict):
            return {}
        return {
            str(k).strip(): item
            for k, item in value.items()
            if str(k).strip()
        }
