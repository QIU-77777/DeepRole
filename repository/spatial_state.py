"""空间运行状态的本地 JSON 存储。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from models.spatial import SpatialPlayerState, SpatialState, apply_npc_schedules, apply_story_environment, StoryTime
from repository.config import CHARACTERS_DIR

STATE_PATH = CHARACTERS_DIR / "spatial_state.json"


def default_spatial_state() -> SpatialState:
    return apply_story_environment(apply_npc_schedules(SpatialState(
        story_time=StoryTime(),
        player=SpatialPlayerState(),
        npc_locations={"linxi": "clubroom", "shenzhiyi": "clubroom"},
    )))


def read_spatial_state() -> SpatialState:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default_spatial_state()
    try:
        return SpatialState.model_validate(payload)
    except (TypeError, ValueError):
        return default_spatial_state()


def write_spatial_state(state: SpatialState) -> SpatialState:
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(f"{STATE_PATH.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, STATE_PATH)
    return state


def update_player_snapshot(*, map_id: str, x: float, y: float) -> SpatialState:
    state = read_spatial_state()
    updated = state.model_copy(update={
        "player": state.player.model_copy(update={"map_id": map_id, "x": x, "y": y}),
    })
    return write_spatial_state(updated)


def reset_spatial_state() -> SpatialState:
    return write_spatial_state(default_spatial_state())
