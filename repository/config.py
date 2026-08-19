"""集中配置管理

静态应用常量从项目根目录的 config.toml 加载。
部署相关配置（密钥、模型选择）在 .env 中。

注意：get_agent_names() 每次调用都会扫描目录，
确保 reset_game() 和 load 命令后能立即反映新的角色列表。
"""

import tomllib
from pathlib import Path

from repository.user_context import current_user_id


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def runtime_dir() -> Path:
    """当前用户的运行时数据目录：data/runtime/{user_id}。"""
    return PROJECT_ROOT / "data" / "runtime" / current_user_id.get()


def characters_dir() -> Path:
    """当前用户的角色目录：data/runtime/{user_id}/characters。"""
    return runtime_dir() / "characters"

with open(PROJECT_ROOT / "config.toml", "rb") as _f:
    _cfg = tomllib.load(_f)

# 记忆整理
CONSOLIDATION_TEMPERATURE: float = _cfg["consolidation"]["temperature"]
CONSOLIDATION_MAX_TOKENS: int | None = _cfg["consolidation"]["max_tokens"] or None
# 向量检索
EPISODE_SEARCH_LIMIT: int = _cfg["vector"]["episode_search_limit"]
UNDERSTANDING_SEARCH_LIMIT: int = _cfg["vector"]["understanding_search_limit"]
VECTOR_CANDIDATE_LIMIT: int = _cfg["vector"]["vector_candidate_limit"]
RERANK_TOP_N: int = _cfg["vector"]["rerank_top_n"]
RELEVANCE_WEIGHT: float = _cfg["vector"]["relevance_weight"]
RECENCY_WEIGHT: float = _cfg["vector"]["recency_weight"]
IMPORTANCE_WEIGHT: float = _cfg["vector"]["importance_weight"]
RECENCY_HALF_LIFE_DAYS: float = _cfg["vector"]["recency_half_life_days"]
RECENCY_DATE_WEIGHT: float = _cfg["vector"]["recency_date_weight"]
RECENCY_RECALL_WEIGHT: float = _cfg["vector"]["recency_recall_weight"]
HYBRID_SEARCH_ENABLED: bool = _cfg["vector"]["hybrid_search_enabled"]
BM25_CANDIDATE_LIMIT: int = _cfg["vector"]["bm25_candidate_limit"]
VECTOR_RELEVANCE_WEIGHT: float = _cfg["vector"]["vector_relevance_weight"]
BM25_RELEVANCE_WEIGHT: float = _cfg["vector"]["bm25_relevance_weight"]

# Agent 运行
AGENT_RUN_TIMEOUT_SECONDS: int = _cfg["agent"]["run_timeout_seconds"]
AGENT_RUN_MAX_ATTEMPTS: int = _cfg["agent"]["max_attempts"]
AGENT_TEMPERATURE: float = _cfg["agent"]["temperature"]
STATE_UPDATER_TEMPERATURE: float = _cfg["agent"]["state_updater_temperature"]
STATE_UPDATER_OUTPUT_RETRIES: int = _cfg["agent"]["state_updater_output_retries"]

# 外部 HTTP 服务
EMBEDDING_REQUEST_TIMEOUT_SECONDS: float = _cfg["embedding"]["request_timeout_seconds"]
RERANK_REQUEST_TIMEOUT_SECONDS: float = _cfg["rerank"]["request_timeout_seconds"]

# 文本后处理
MAX_CHOICE_CHARS: int = _cfg["text"]["max_choice_chars"]
MAX_EPISODE_KEYWORDS: int = _cfg["text"]["max_episode_keywords"]

# 多轮消息高低水位截断
HISTORY_HIGH: int = _cfg["history"]["history_high"]
HISTORY_LOW: int = _cfg["history"]["history_low"]
HISTORY_RAW_SCAN_TURNS: int = _cfg["history"]["raw_scan_turns"]


def character_path(character_name: str, *subpaths: str) -> str:
    """构建当前用户下角色的数据路径。

    Args:
        character_name: 角色名称
        *subpaths: 子路径组件

    Returns:
        完整路径字符串

    Example:
        >>> character_path("lilith", "soul.md")
        "/abs/path/to/data/runtime/{user_id}/characters/lilith/soul.md"
    """
    return str(characters_dir() / character_name / Path(*subpaths))


def get_agent_names(include_narrator: bool = True) -> list[str]:
    """获取角色名称列表（每次动态扫描当前用户的 data/runtime/{user_id}/characters/）

    Args:
        include_narrator: 是否包含 narrator，默认 True

    Returns:
        角色名称列表，按字母顺序排序
    """
    chars_dir = characters_dir()
    if not chars_dir.exists():
        return []
    agents = sorted(
        d.name
        for d in chars_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_")
    )
    if not include_narrator:
        agents = [name for name in agents if name != "narrator"]
    return agents
