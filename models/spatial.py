"""空间化垂直切片的纯领域状态模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MapId = Literal["campus_center", "arts_hallway", "clubroom", "rooftop"]
SpatialNpcId = Literal["linxi", "shenzhiyi"]
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class StoryTime(BaseModel):
    """可回滚的叙事时间，不依赖宿主机时钟。"""

    model_config = ConfigDict(frozen=True)

    season: str = "秋季"
    week: int = Field(default=1, ge=1)
    weekday: str = "周三"
    minute_of_day: int = Field(default=18 * 60 + 30, ge=0, le=23 * 60 + 59)

    @field_validator("weekday")
    @classmethod
    def _validate_weekday(cls, value: str) -> str:
        if value not in WEEKDAYS:
            raise ValueError(f"未知星期：{value}")
        return value

    @property
    def display(self) -> str:
        hour, minute = divmod(self.minute_of_day, 60)
        return f"{self.season} · 第 {self.week} 周 · {self.weekday} {hour:02d}:{minute:02d}"


class SpatialPlayerState(BaseModel):
    map_id: MapId = "campus_center"
    x: float = 176.0
    y: float = 336.0
    spawn_id: str = "campus_center_start"


class SpatialState(BaseModel):
    schema_version: int = 1
    story_time: StoryTime = Field(default_factory=StoryTime)
    player: SpatialPlayerState = Field(default_factory=SpatialPlayerState)
    active_followers: list[str] = Field(default_factory=list)
    npc_locations: dict[str, MapId] = Field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.story_time.display


SPATIAL_NPC_IDS: tuple[str, ...] = ("linxi", "shenzhiyi")


def advance_story_time(story_time: StoryTime, minutes: int) -> StoryTime:
    """推进叙事时间；只接受系统已校验的非负时长。"""
    if minutes < 0:
        raise ValueError("叙事时间不能倒退")
    total = story_time.minute_of_day + minutes
    day_offset, minute_of_day = divmod(total, 24 * 60)
    weekday_index = WEEKDAYS.index(story_time.weekday)
    absolute_day = weekday_index + day_offset
    week = story_time.week + absolute_day // 7
    weekday = WEEKDAYS[absolute_day % 7]
    return story_time.model_copy(update={"week": week, "weekday": weekday, "minute_of_day": minute_of_day})


SPATIAL_TRANSITIONS: dict[tuple[MapId, str], dict[str, object]] = {
    ("campus_center", "to_arts_hallway"): {"target": "arts_hallway", "spawn_id": "arts_hallway_west", "x": 80.0, "y": 336.0, "minutes": 10},
    ("arts_hallway", "to_campus_center"): {"target": "campus_center", "spawn_id": "campus_center_east", "x": 944.0, "y": 336.0, "minutes": 10},
    ("arts_hallway", "to_clubroom"): {"target": "clubroom", "spawn_id": "clubroom_door", "x": 400.0, "y": 432.0, "minutes": 5},
    ("clubroom", "to_arts_hallway"): {"target": "arts_hallway", "spawn_id": "arts_hallway_clubroom", "x": 336.0, "y": 112.0, "minutes": 5},
    ("clubroom", "to_rooftop"): {"target": "rooftop", "spawn_id": "rooftop_stairs", "x": 80.0, "y": 240.0, "minutes": 5},
    ("rooftop", "to_clubroom"): {"target": "clubroom", "spawn_id": "clubroom_rooftop", "x": 528.0, "y": 208.0, "minutes": 5},
}


def transition_spatial_state(state: SpatialState, *, from_map: MapId, exit_id: str) -> SpatialState:
    """验证并应用一个地图出口转换。"""
    if state.player.map_id != from_map:
        raise ValueError("当前地图与请求不一致")
    transition = SPATIAL_TRANSITIONS.get((from_map, exit_id))
    if transition is None:
        raise KeyError(exit_id)
    return state.model_copy(update={
        "story_time": advance_story_time(state.story_time, int(transition["minutes"])),
        "player": state.player.model_copy(update={
            "map_id": str(transition["target"]),
            "spawn_id": str(transition["spawn_id"]),
            "x": float(transition["x"]),
            "y": float(transition["y"]),
        }),
    })


def move_npc(state: SpatialState, *, npc_id: str, destination: MapId) -> SpatialState:
    """通过语义 waypoint 移动 NPC；领域层不接受坐标或文本传送。"""
    if npc_id not in SPATIAL_NPC_IDS:
        raise KeyError(npc_id)
    locations = dict(state.npc_locations)
    locations[npc_id] = destination
    return state.model_copy(update={"npc_locations": locations})
