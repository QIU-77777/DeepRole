"""旁白文件 ↔ 状态仓库：status / world_schedule 的读写。

narrator 无 memory / draft，字段与角色不同（场景 / 角色位置 / 待触发事件 / world_schedule）。
方法只吃原始值，LLM DTO 的拆解留在 NarratorService（反腐层），storage 不依赖 agents。
"""

from models import status_fields
from repository.agent_files import read_agent_file, read_sidecar_json, write_sidecar_json
from repository.status_file import (
    FileUpdateResult,
    update_status_allow_new_field,
)

_NAME = "narrator"
WORLD_SCHEDULE_FILENAME = "world_schedule.json"


class NarratorRepository:
    """旁白文件读写仓库；status / world_schedule 均为运行时动态文件，按需直读不缓存。

    刻意不持有 soul 缓存（与 CharacterRepository 不对称）：narrator 的 soul 只有一个用途——
    建 narrator agent 时烤进 system prompt（agents.factory.reload_conversation_agent），
    建好的 agent 进 _conversation_agents registry 复用，每轮 get_conversation_agent 不重读
    soul.md，load/reset 时 rebuild agent 自然刷新。soul 已被 agent registry 缓存，repo 再缓存
    既重复又无调用方。character 之所以保留 soul 缓存，是因为它每轮还会 character_repo.load(name)
    取一个带 soul 的领域实体（命中缓存），而 narrator 没有这条 load 路径。
    """

    # ── 读 ──

    def read_status_text(self) -> str:
        return read_agent_file(_NAME, "status.md")

    def read_world_schedule(self) -> dict:
        return read_sidecar_json(_NAME, WORLD_SCHEDULE_FILENAME)

    def read_world_schedule_text(self) -> str:
        return read_agent_file(_NAME, WORLD_SCHEDULE_FILENAME)

    # ── 写回 ──

    def write_scene(
        self,
        *,
        scene: str,
        current_time: str,
        character_locations: dict[str, str] | None,
    ) -> list[FileUpdateResult]:
        """同步场景 / 当前时间 / 角色位置（narrator 独占维护的派生字段）。"""
        results = [
            update_status_allow_new_field(_NAME, status_fields.SCENE, scene),
            update_status_allow_new_field(_NAME, status_fields.CURRENT_TIME, current_time),
        ]
        if character_locations:
            lines = [f"- {name}：{loc}" for name, loc in character_locations.items()]
            results.append(
                update_status_allow_new_field(
                    _NAME, status_fields.CHARACTER_LOCATIONS, "\n".join(lines)
                )
            )
        return results

    def write_world_schedule(self, schedule: dict) -> FileUpdateResult:
        write_sidecar_json(_NAME, WORLD_SCHEDULE_FILENAME, schedule)
        return FileUpdateResult(
            file=WORLD_SCHEDULE_FILENAME,
            target="world_schedule",
            operation="replace",
        )


narrator_repo = NarratorRepository()
