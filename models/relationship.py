"""关系状态领域模型；只保存可解释的阶段、标签和自然语言描述。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelationshipEntry(BaseModel):
    stage: str = "未知"
    tags: list[str] = Field(default_factory=list)
    description: str = ""


class RelationshipState(BaseModel):
    schema_version: int = 1
    characters: dict[str, RelationshipEntry] = Field(default_factory=dict)


_STAGE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("恋人", "恋人"),
    ("暧昧", "暧昧"),
    ("亲密", "亲密"),
    ("朋友", "朋友"),
    ("熟悉", "熟人"),
    ("认识", "认识"),
    ("陌生", "陌生"),
)


def relationship_entry_from_description(description: str) -> RelationshipEntry:
    """从角色公开写回的关系描述派生展示阶段和可解释标签。"""
    text = " ".join(description.split())
    stage = next((stage for keyword, stage in _STAGE_KEYWORDS if keyword in text), "未知")
    tags: list[str] = []
    for keyword, tag in (("信任", "信任"), ("依赖", "依赖"), ("好感", "好感"), ("冲突", "冲突"), ("浪漫", "浪漫")):
        if keyword in text:
            tags.append(tag)
    return RelationshipEntry(stage=stage, tags=tags, description=text)
