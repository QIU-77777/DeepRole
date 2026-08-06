"""领域实体：长期记忆事件 EpisodeMemory 与稳定理解 Understanding。

纯数据模型，零 I/O；仅依赖 shared 的纯函数。

与 agents/llm_schema.py 里的 LLM 契约（LLMEpisodeMemory / LLMUnderstandingEntry）
刻意保持独立、不继承：契约界定"LLM 能填哪些字段"，实体多出 id / memory_owner
等系统字段，二者的映射在 consolidation 流程显式完成（反腐层）。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.dates import canonical_cn_date


class EpisodeMemory(BaseModel):
    """memory.jsonl 的一行记录，对应一条结构化长期记忆事件。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = ""
    date: str = ""
    time: str = ""
    location: str = ""
    participants: str = ""
    keywords: list[str] = Field(default_factory=list)
    importance: int = 3
    content: str = ""
    memory_owner: str = ""
    title: str = ""
    raw_dialogue: str = ""
    last_recalled_at: str = Field(
        default_factory=lambda data: canonical_cn_date(str(data.get("date", "")))
        or str(data.get("date", "")).strip()
    )

    @field_validator("keywords", mode="before")
    @classmethod
    def _clean_keywords(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(k).strip() for k in value if str(k).strip()]

    @field_validator("importance", mode="before")
    @classmethod
    def _clamp_importance(cls, value: object) -> int:
        try:
            return max(1, min(5, int(value)))
        except (TypeError, ValueError):
            return 3


class UnderstandingHistoryEntry(BaseModel):
    """Understanding 聚合下的值对象：一次理解更新的快照。

    注意：content = 理解更新后的内容快照（非事件内容）；
    episode_id / date / title 只是触发这次更新的那条 episode 的出处标注。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    episode_id: str = ""
    date: str = ""
    title: str = ""
    content: str = ""


class Understanding(BaseModel):
    """角色形成的稳定理解，记录持久信念或互动模式，而非单次事件。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = ""
    memory_owner: str = ""
    subject: str = ""
    keywords: list[str] = Field(default_factory=list)
    content: str = ""
    linked_episodes: list[str] = Field(default_factory=list)
    history: list[UnderstandingHistoryEntry] = Field(default_factory=list)
