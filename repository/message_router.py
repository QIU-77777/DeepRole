"""消息路由系统 - 维护每个角色的独立对话历史"""

from repository.runtime_state import (
    extract_player_name,
    increment_turn_counter,
    read_player_name,
    read_turn_counter,
    write_player_name,
)
from repository.history import append_message


class MessageRouter:
    """消息路由系统 - 维护每个角色的独立对话历史"""

    async def _broadcast_message(
        self,
        targets: list[str],
        message: dict,
        *,
        turn: int | None = None,
        visible_to: list[str] | None = None,
    ) -> int:
        """只写入 narrator 的 jsonl（单一数据源）。

        每条消息都带上当前 narrator turn 号，供 `EpisodeClosureDetector` 与
        `consolidate_agent` 按 turn 切片。narrator 发言会开启新 turn；之后的角色
        回应与下一次玩家输入继续沿用该 turn，直到下一条 narrator 发言。
        """
        visible = (visible_to if visible_to is not None else targets).copy()
        if "narrator" not in visible:
            visible.append("narrator")
        visible = list(dict.fromkeys(visible))

        assigned_turn = read_turn_counter() if turn is None else int(turn)
        message["visible_to"] = visible
        message["turn"] = assigned_turn
        await append_message(message)
        return assigned_turn

    async def broadcast_player_message(
        self,
        targets: list[str],
        content: str,
        *,
        visible_to: list[str] | None = None,
    ) -> int:
        turn = read_turn_counter()
        player_name = read_player_name()
        if not player_name and turn == 1 and (player_name := extract_player_name(content)):
            write_player_name(player_name)

        raw_content = f"## {player_name}\n{content.strip()}" if player_name else content
        return await self._broadcast_message(
            targets,
            {
                "role": "player",
                "content": raw_content,
            },
            turn=turn,
            visible_to=visible_to,
        )

    async def broadcast_agent_response(
        self,
        agent_name: str,
        targets: list[str],
        content: str,
        *,
        visible_to: list[str] | None = None,
    ) -> int:
        turn = increment_turn_counter() if agent_name == "narrator" else None
        return await self._broadcast_message(
            targets,
            {"role": agent_name, "content": content},
            turn=turn,
            visible_to=visible_to,
        )

    async def broadcast_narrator_output(
        self,
        targets: list[str],
        output: dict,
        *,
        visible_to: list[str] | None = None,
    ) -> int:
        """Broadcast structured narrator output as raw history fields."""
        turn = increment_turn_counter()
        message = dict(output)
        message["role"] = "narrator"
        return await self._broadcast_message(
            targets,
            message,
            turn=turn,
            visible_to=visible_to,
        )


message_router = MessageRouter()
