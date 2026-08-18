import pytest

from models.spatial import StoryTime, SpatialState, advance_story_time, apply_npc_schedules, apply_story_environment, available_spatial_events, end_story_day, move_npc, transition_spatial_state, trigger_spatial_event
import repository.spatial_state as spatial_state


def test_story_time_advances_across_week_boundary() -> None:
    current = StoryTime(week=1, weekday="周日", minute_of_day=23 * 60 + 50)
    updated = advance_story_time(current, 20)

    assert updated.week == 2
    assert updated.weekday == "周一"
    assert updated.minute_of_day == 10


def test_spatial_state_round_trip_uses_local_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(spatial_state, "STATE_PATH", tmp_path / "spatial_state.json")
    monkeypatch.setattr(spatial_state, "CHARACTERS_DIR", tmp_path)

    state = spatial_state.default_spatial_state()
    spatial_state.write_spatial_state(state)
    restored = spatial_state.read_spatial_state()

    assert restored == state
    assert restored.player.map_id == "campus_center"


def test_transition_validates_exit_and_advances_story_time() -> None:
    state = spatial_state.default_spatial_state()
    updated = transition_spatial_state(state, from_map="campus_center", exit_id="to_arts_hallway")

    assert updated.player.map_id == "arts_hallway"
    assert updated.player.spawn_id == "arts_hallway_west"
    assert updated.story_time.minute_of_day == 18 * 60 + 40

    with pytest.raises(KeyError):
        transition_spatial_state(updated, from_map="arts_hallway", exit_id="missing")


def test_npc_move_uses_semantic_destination_only() -> None:
    state = SpatialState(npc_locations={"linxi": "clubroom"})
    moved = move_npc(state, npc_id="linxi", destination="rooftop")

    assert moved.npc_locations == {"linxi": "rooftop"}


def test_npc_move_rejects_unknown_npc() -> None:
    with pytest.raises(KeyError):
        move_npc(SpatialState(), npc_id="unknown", destination="rooftop")


def test_end_story_day_rolls_to_next_morning() -> None:
    state = SpatialState(
        story_time=StoryTime(week=1, weekday="周三", minute_of_day=18 * 60 + 30),
    )
    updated = end_story_day(state)

    assert updated.story_time.week == 1
    assert updated.story_time.weekday == "周四"
    assert updated.story_time.minute_of_day == 8 * 60
    assert updated.player.map_id == "campus_center"


def test_npc_schedule_projects_offscreen_locations_from_story_time() -> None:
    evening = SpatialState(story_time=StoryTime(minute_of_day=21 * 60))
    projected = apply_npc_schedules(evening)

    assert projected.npc_locations == {"linxi": "campus_center", "shenzhiyi": "rooftop"}


def test_spatial_event_rules_are_deterministic_and_one_shot() -> None:
    state = SpatialState(
        story_time=StoryTime(minute_of_day=18 * 60 + 30),
        player={"map_id": "clubroom", "x": 0, "y": 0, "spawn_id": "clubroom_door"},
    )
    events = available_spatial_events(state)
    assert [event.event_id for event in events] == ["clubroom_evening_rehearsal"]

    triggered = trigger_spatial_event(state, events[0].event_id)
    assert available_spatial_events(triggered) == []
    with pytest.raises(KeyError):
        trigger_spatial_event(triggered, events[0].event_id)


def test_weather_and_day_phase_come_from_story_time() -> None:
    state = SpatialState(story_time=StoryTime(week=1, weekday="周四", minute_of_day=23 * 60))
    projected = apply_story_environment(state)

    assert projected.weather == "晴朗"
    assert projected.day_phase == "夜间"
