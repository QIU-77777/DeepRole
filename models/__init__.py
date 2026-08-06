"""领域模型层（最内层）：实体与值对象，零 I/O，依赖单向向内。

自包含——只依赖本层（如 models.dates 游戏日期值对象）与第三方库，绝不 import
任何外层（repository / app / server）。原 shared/ 已拆散，纯领域规则归入本层。
"""

from models.character import Character
from models.memory import (
    EpisodeMemory,
    Understanding,
    UnderstandingHistoryEntry,
)

__all__ = [
    "Character",
    "EpisodeMemory",
    "Understanding",
    "UnderstandingHistoryEntry",
]
