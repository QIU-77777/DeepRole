"""记忆向量索引重建。

将 memory.jsonl 的结构化记忆记录写入向量库。
"""

from repository.log_config.memory import memory_logger as logger
from repository.memory_store import (
    memory_jsonl_path,
    read_memory_jsonl,
    read_understandings,
)
from repository.config import get_agent_names
from repository.vector_store import vector_store

DEFAULT_REBUILD_BATCH_SIZE = 64


async def rebuild_memory_index(
    agent_name: str | None = None,
    *,
    clear_existing: bool = True,
    batch_size: int = DEFAULT_REBUILD_BATCH_SIZE,
) -> None:
    """从 memory.jsonl 重建向量索引。

    Args:
        agent_name: 指定角色时只重建该角色；为 None 时重建所有非 narrator 角色。
        clear_existing: 是否在重建前清理目标角色已有索引。
        batch_size: 每批 embedding 请求与 DB 写入的记忆条数。
    """
    if agent_name is not None:
        agents = [agent_name]
        if clear_existing:
            await vector_store.delete(agent_name)
    else:
        agents = get_agent_names(include_narrator=False)
        if clear_existing:
            await vector_store.delete_all_agents(agents)

    for agent in agents:
        if not memory_jsonl_path(agent).exists():
            logger.info("[indexer] 跳过 %s：未找到 memory.jsonl", agent)
            continue

        records = read_memory_jsonl(agent)
        inserted = await vector_store.add_episodes(records, batch_size=batch_size)

        logger.info("[indexer] %s 索引完成：records=%s, inserted=%s", agent, len(records), inserted)

    if clear_existing:
        await _rebuild_understanding_index_for_agents(agents)


async def rebuild_understanding_index(
    agent_name: str | None = None,
) -> None:
    """从 understanding.jsonl 重建 Understanding 向量索引。

    Args:
        agent_name: 指定角色时只重建该角色；为 None 时重建所有非 narrator 角色。
    """
    agents = (
        [agent_name]
        if agent_name is not None
        else get_agent_names(include_narrator=False)
    )
    await _rebuild_understanding_index_for_agents(agents)


async def _rebuild_understanding_index_for_agents(agents: list[str]) -> None:
    for agent in agents:
        understandings = read_understandings(agent)
        if not understandings:
            logger.info("[indexer] 跳过 %s：未找到 understanding.jsonl 或为空", agent)
            continue
        inserted = await vector_store.add_understandings(list(understandings.values()))
        logger.info(
            "[indexer] %s Understanding 索引完成：count=%s, inserted=%s",
            agent, len(understandings), inserted,
        )
