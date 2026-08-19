"""当前关系状态文件；不参与记忆召回，也不保存隐藏好感数值。"""

from __future__ import annotations

import json
import os

from models.relationship import RelationshipEntry, RelationshipState, relationship_entry_from_description
from repository.config import CHARACTERS_DIR
from repository.agent_files import read_agent_file
from repository.status_file import extract_status_field

STATE_PATH = CHARACTERS_DIR / "relationship_state.json"


def read_relationship_state() -> RelationshipState:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return RelationshipState()
    try:
        return RelationshipState.model_validate(payload)
    except (TypeError, ValueError):
        return RelationshipState()


def write_relationship_state(state: RelationshipState) -> RelationshipState:
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(f"{STATE_PATH.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, STATE_PATH)
    return state


def sync_relationship_from_status(agent_name: str, fields: dict[str, str]) -> RelationshipEntry | None:
    description = str(fields.get("和玩家的关系", "")).strip()
    if not description:
        return None
    state = read_relationship_state()
    entry = relationship_entry_from_description(description)
    characters = dict(state.characters)
    characters[agent_name] = entry
    write_relationship_state(state.model_copy(update={"characters": characters}))
    return entry


def reset_relationship_state() -> RelationshipState:
    return write_relationship_state(RelationshipState())


def rebuild_relationship_state(agent_names: list[str]) -> RelationshipState:
    characters: dict[str, RelationshipEntry] = {}
    for agent_name in agent_names:
        status = read_agent_file(agent_name, "status.md")
        description = extract_status_field(status, "和玩家的关系")
        if description:
            characters[agent_name] = relationship_entry_from_description(description)
    return write_relationship_state(RelationshipState(characters=characters))
