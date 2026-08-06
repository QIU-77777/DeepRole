"""角色目录基础文件操作 - 通用读取、JSON sidecar、备份。

status.md 的 section 引擎 / 事件队列 / 状态写回见 storage/status_file.py；
turn 计数 / 玩家名等运行时 sidecar 见 storage/runtime_state.py；
memory_draft.jsonl 读写见 storage/memory_store.py。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from repository.config import character_path


# ===== 通用文件读取 =====


def load_text(path: Path) -> str:
    """读取文本文件内容，文件不存在返回空字符串。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_agent_file(agent_name: str, filename: str) -> str:
    """读取角色目录下的指定文件内容，不存在返回空字符串。"""
    path = character_path(agent_name, filename)
    return load_text(Path(path))


# ===== JSON sidecar =====


def read_sidecar_json(agent_name: str, filename: str) -> dict:
    """读取角色目录下的 JSON sidecar 文件，解析失败返回空字典。"""
    path = Path(character_path(agent_name, filename))
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_sidecar_json(agent_name: str, filename: str, data: dict) -> None:
    """将 data 写入角色目录下的 JSON sidecar 文件。"""
    path = Path(character_path(agent_name, filename))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ===== 备份 =====


def cleanup_old_backups(bak_dir: Path, pattern: str, max_count: int = 10) -> int:
    """清理旧备份文件，只保留最近的 max_count 个，返回删除数量。"""
    bak_files = sorted(bak_dir.glob(pattern), key=lambda f: f.stat().st_mtime)
    deleted = 0
    if len(bak_files) > max_count:
        for old_bak in bak_files[:-max_count]:
            old_bak.unlink()
            deleted += 1
    return deleted


def backup_file(src: Path, agent_name: str, prefix: str, max_backups: int = 10) -> Path:
    """备份文件到 agent 的 bak 目录，保留最近 max_backups 个备份。

    Args:
        src: 源文件路径
        agent_name: 角色名
        prefix: 备份文件前缀，如 "Memory"、"user"
        max_backups: 最大保留备份数，默认 10

    Returns:
        备份文件的完整路径
    """
    bak_dir = Path(character_path(agent_name, "bak"))
    bak_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    bak_path = bak_dir / f"{prefix}_{ts}_pre.md"
    shutil.copy2(src, bak_path)

    cleanup_old_backups(bak_dir, f"{prefix}_*_pre.md", max_count=max_backups)
    return bak_path
