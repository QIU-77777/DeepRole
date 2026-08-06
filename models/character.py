"""领域实体：游戏角色 Character + 从 soul.md 派生显示信息的纯规则。

极薄实体——只承载本存档生命周期内不变的数据：name + 只读 soul 文本。
可变 / 共享状态（status.md、memory）不进实体：它们回合中被多方改写、且人工可编辑，
持有快照会过期，统一经 CharacterRepository 按需读写。

`get_display_name` / `extract_identity` 是"从 soul 文本派生角色显示信息"的领域规则
（纯函数、零 I/O），故与实体同处领域层。
"""

import re
from dataclasses import dataclass

_IDENTITY_PATTERN = re.compile(r"<identity>\s*(.+?)\s*</identity>", re.DOTALL)


def get_display_name(agent_name: str, soul_content: str) -> str:
    """从 soul.md 内容提取中文显示名，回退到 agent_name。"""
    role_match = re.search(r"<role>\s*([^\n<]+)", soul_content)
    if role_match:
        name_match = re.match(r"([一-鿿·]+)", role_match.group(1).strip())
        if name_match:
            return name_match.group(1)
    title_match = re.search(r"^#\s+(.+)$", soul_content, re.MULTILINE)
    if title_match:
        return title_match.group(1).strip()
    return agent_name


def extract_identity(soul_content: str) -> str:
    """从 soul.md 的 <identity> 块提取一行公开身份标签；缺失或为空返回空字符串。"""
    if not soul_content:
        return ""
    match = _IDENTITY_PATTERN.search(soul_content)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


@dataclass(frozen=True)
class Character:
    """游戏角色领域实体（不可变数据）。"""

    name: str
    soul: str  # soul.md 全文，只读 prompt 资源

    @property
    def display_name(self) -> str:
        """从 soul.md 提取中文显示名，回退到 name。"""
        return get_display_name(self.name, self.soul)
