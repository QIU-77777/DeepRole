"""全局运行时状态 sidecar：narrator turn 计数 + 玩家显示名（跨角色共享）。"""

import json
import re
from functools import cache

from repository.config import CHARACTERS_DIR
from repository.agent_files import load_text

_TURN_COUNTER_FILENAME = ".turn_counter.json"
PLAYER_NAME_FILENAME = ".player_name"
_PLAYER_NAME_RE = re.compile(r"(?:我叫|我的名字是|名字是|叫我)\s*([^\s，,。！？、；;：:\n]+)")


def extract_player_name(content: str) -> str:
    """从玩家自我介绍文本中提取显示名。"""
    match = _PLAYER_NAME_RE.search(content or "")
    return match.group(1).strip() if match else ""


@cache
def read_player_name() -> str:
    """读取全局玩家显示名；不存在返回空字符串。"""
    return load_text(CHARACTERS_DIR / PLAYER_NAME_FILENAME).strip()


def write_player_name(player_name: str) -> None:
    """写入全局玩家显示名。空值不写入。"""
    if not (name := player_name.strip()):
        return
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    (CHARACTERS_DIR / PLAYER_NAME_FILENAME).write_text(name, encoding="utf-8")
    read_player_name.cache_clear()


def read_turn_counter() -> int:
    """读取全局 narrator turn 计数器；不存在或解析失败返回 0。"""
    path = CHARACTERS_DIR / _TURN_COUNTER_FILENAME
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("turn", 0))
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return 0


def write_turn_counter(turn: int) -> None:
    """将 narrator turn 计数写入全局 sidecar。"""
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    path = CHARACTERS_DIR / _TURN_COUNTER_FILENAME
    path.write_text(json.dumps({"turn": int(turn)}, ensure_ascii=False), encoding="utf-8")


def increment_turn_counter() -> int:
    """开启下一段 narrator turn（单进程足够）并返回新的 turn 号。"""
    new_turn = read_turn_counter() + 1
    write_turn_counter(new_turn)
    return new_turn
