"""角色文件 ↔ 实体仓库：soul 加载（带缓存）+ status / memory 写回策略。

只保留带 value-add 的写回（apply_status_fields 的逐字段跳空容错、append_memory 的
normalize+turn 标记）；纯转发的事件段维护（打算 add/triggered）由 Service 直调
status_file 机制函数，不再在此包薄壳。不含编排（搜记忆 / 跑 SDK 在 Service）。
"""

from models import Character
from repository.log_config.routing import routing_logger
from repository.memory_store import normalize
from repository.agent_files import read_agent_file
from repository.memory_store import append_memory_draft
from repository.runtime_state import read_turn_counter
from repository.status_file import FileUpdateResult, update_status


class CharacterRepository:
    """游戏角色的文件读写仓库；soul 缓存随存档生命周期，load/reset 后失效。"""

    def __init__(self) -> None:
        self._soul_cache: dict[str, str] = {}

    # ── 读 ──

    def load(self, name: str) -> Character:
        """读 soul.md（带缓存）并返回 Character 实体。"""
        soul = self._soul_cache.get(name)
        if soul is None:
            soul = read_agent_file(name, "soul.md")
            self._soul_cache[name] = soul
        return Character(name=name, soul=soul)

    def invalidate(self, name: str | None = None) -> None:
        """存档恢复 / reset 后清空 soul 缓存（name=None 清全部）。"""
        if name is None:
            self._soul_cache.clear()
        else:
            self._soul_cache.pop(name, None)

    # ── 写回策略 ──

    def apply_status_fields(self, name: str, fields: dict[str, str]) -> list[FileUpdateResult]:
        """按字段合并更新 status.md；空值跳过。事件段的整段覆写拦截由 update_status 负责。"""
        results: list[FileUpdateResult] = []
        for field, content in fields.items():
            if not content:
                continue
            try:
                results.append(update_status(name, field, str(content)))
            except Exception as e:
                routing_logger.error(f"[{name}] status[{field}] 失败: {e}")
        return results

    def append_memory(self, name: str, text: str) -> FileUpdateResult | None:
        """把本轮 memory 片段 normalize + 标当前 narrator turn 后追加到 memory_draft.jsonl。"""
        if not text:
            return None
        normalized = normalize(text)
        if not normalized:
            return None
        try:
            turn = read_turn_counter()
            append_memory_draft(name, turn, normalized)
            return FileUpdateResult(
                file="memory_draft.jsonl",
                target="长期记忆",
                operation="append",
                appended=normalized,
            )
        except Exception as e:
            routing_logger.error(f"[{name}] memory_draft 写入失败: {e}")
            return None


character_repo = CharacterRepository()
