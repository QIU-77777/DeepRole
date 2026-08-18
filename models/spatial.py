"""空间化垂直切片的纯领域状态模型。"""

from __future__ import annotations

from typing import Literal, cast

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
    weather: str = "多云"
    player: SpatialPlayerState = Field(default_factory=SpatialPlayerState)
    active_followers: list[str] = Field(default_factory=list)
    npc_locations: dict[str, MapId] = Field(default_factory=dict)
    npc_overrides: dict[str, MapId] = Field(default_factory=dict)
    npc_routes: dict[str, list[MapId]] = Field(default_factory=dict)
    triggered_events: list[str] = Field(default_factory=list)

    @property
    def display(self) -> str:
        return self.story_time.display

    @property
    def day_phase(self) -> str:
        minute = self.story_time.minute_of_day
        if minute < 6 * 60:
            return "夜间"
        if minute < 18 * 60:
            return "白天"
        if minute < 22 * 60:
            return "傍晚"
        return "夜间"


SPATIAL_NPC_IDS: tuple[str, ...] = ("linxi", "shenzhiyi")


class SpatialEventDefinition(BaseModel):
    event_id: str
    map_id: MapId
    start_minute: int = Field(ge=0, le=23 * 60 + 59)
    label: str
    prompt: str


SPATIAL_EVENT_DEFINITIONS: tuple[SpatialEventDefinition, ...] = (
    SpatialEventDefinition(
        event_id="clubroom_evening_rehearsal",
        map_id="clubroom",
        start_minute=18 * 60,
        label="晚间排练留下的痕迹",
        prompt="活动室的排练灯还亮着，谱架上留着一页被反复修改的台词。",
    ),
    SpatialEventDefinition(
        event_id="rooftop_after_rehearsal",
        map_id="rooftop",
        start_minute=20 * 60,
        label="天台的风",
        prompt="夜风把楼下的喧闹吹散，栏杆上压着一张没有署名的便签。",
    ),
)


def scheduled_npc_map(npc_id: str, story_time: StoryTime) -> MapId:
    """首版可维护的基础日程：只决定语义地图，不生成逐帧移动。"""
    minute = story_time.minute_of_day
    if npc_id == "linxi":
        return "clubroom" if 17 * 60 <= minute < 21 * 60 else "campus_center"
    if npc_id == "shenzhiyi":
        if 17 * 60 <= minute < 20 * 60:
            return "clubroom"
        if 20 * 60 <= minute < 22 * 60:
            return "rooftop"
        return "arts_hallway"
    raise KeyError(npc_id)


def apply_npc_schedules(state: SpatialState) -> SpatialState:
    """按故事时间投影主要 NPC 的离屏位置。"""
    locations = dict(state.npc_locations)
    for npc_id in SPATIAL_NPC_IDS:
        locations[npc_id] = state.npc_overrides.get(
            npc_id,
            scheduled_npc_map(npc_id, state.story_time),
        )
    return state.model_copy(update={"npc_locations": locations})


def weather_for_story_time(story_time: StoryTime) -> str:
    """首版天气表：仅由故事日期决定，不读取宿主机天气或现实时间。"""
    day_index = (story_time.week - 1) * 7 + WEEKDAYS.index(story_time.weekday)
    return ("多云", "晴朗", "小雨", "晴朗")[day_index % 4]


def apply_story_environment(state: SpatialState) -> SpatialState:
    return state.model_copy(update={"weather": weather_for_story_time(state.story_time)})


def available_spatial_events(state: SpatialState) -> list[SpatialEventDefinition]:
    """返回当前时空满足条件且尚未触发的确定性事件。"""
    triggered = set(state.triggered_events)
    return [
        event
        for event in SPATIAL_EVENT_DEFINITIONS
        if event.event_id not in triggered
        and event.map_id == state.player.map_id
        and state.story_time.minute_of_day >= event.start_minute
    ]


def trigger_spatial_event(state: SpatialState, event_id: str) -> SpatialState:
    available = {event.event_id for event in available_spatial_events(state)}
    if event_id not in available:
        raise KeyError(event_id)
    return state.model_copy(update={
        "triggered_events": [*state.triggered_events, event_id],
    })


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


def find_map_route(start: MapId, destination: MapId) -> list[MapId]:
    """在互联场景图上计算最短语义路线；路线不含坐标。"""
    if start == destination:
        return [start]
    queue: list[MapId] = [start]
    parents: dict[MapId, MapId | None] = {start: None}
    while queue:
        current = queue.pop(0)
        neighbors = [
            target
            for (source, _exit_id), transition in SPATIAL_TRANSITIONS.items()
            if source == current
            for target in [transition["target"]]
        ]
        for neighbor in neighbors:
            next_map = cast(MapId, neighbor)
            if next_map in parents:
                continue
            parents[next_map] = current
            if next_map == destination:
                route: list[MapId] = [destination]
                cursor: MapId | None = current
                while cursor is not None:
                    route.append(cursor)
                    cursor = parents[cursor]
                route.reverse()
                return route
            queue.append(next_map)
    raise KeyError(destination)


def transition_spatial_state(state: SpatialState, *, from_map: MapId, exit_id: str) -> SpatialState:
    """验证并应用一个地图出口转换。"""
    if state.player.map_id != from_map:
        raise ValueError("当前地图与请求不一致")
    transition = SPATIAL_TRANSITIONS.get((from_map, exit_id))
    if transition is None:
        raise KeyError(exit_id)
    updated = state.model_copy(update={
        "story_time": advance_story_time(state.story_time, int(transition["minutes"])),
        "player": state.player.model_copy(update={
            "map_id": str(transition["target"]),
            "spawn_id": str(transition["spawn_id"]),
            "x": float(transition["x"]),
            "y": float(transition["y"]),
        }),
    })
    return apply_story_environment(apply_npc_schedules(updated))


def move_npc(state: SpatialState, *, npc_id: str, destination: MapId) -> SpatialState:
    """通过语义 waypoint 移动 NPC；领域层不接受坐标或文本传送。"""
    if npc_id not in SPATIAL_NPC_IDS:
        raise KeyError(npc_id)
    locations = dict(state.npc_locations)
    current = locations.get(npc_id, destination)
    route = find_map_route(current, destination)
    locations[npc_id] = destination
    overrides = dict(state.npc_overrides)
    overrides[npc_id] = destination
    routes = dict(state.npc_routes)
    routes[npc_id] = route
    return state.model_copy(update={
        "npc_locations": locations,
        "npc_overrides": overrides,
        "npc_routes": routes,
    })


def end_story_day(state: SpatialState) -> SpatialState:
    """结束当前叙事日并回到第二天早晨的校园入口。"""
    next_time = advance_story_time(
        state.story_time,
        (24 * 60 - state.story_time.minute_of_day) + 8 * 60,
    )
    updated = state.model_copy(update={
        "story_time": next_time,
        "player": state.player.model_copy(update={
            "map_id": "campus_center",
            "spawn_id": "campus_center_start",
            "x": 176.0,
            "y": 336.0,
        }),
        "npc_overrides": {},
        "npc_routes": {},
    })
    return apply_story_environment(apply_npc_schedules(updated))
