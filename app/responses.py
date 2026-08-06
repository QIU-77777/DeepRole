"""LLM 回复的后处理：剥离 thinking 标签、规范空白、判定有效性。

用例层助手——只被对话编排（conversation_flow / narrator_service）消费，不涉 I/O。
"""

import re

_THINKING_PATTERN = re.compile(
    r"<thinking>.*?</thinking>|<think>.*?</think>|<reasoning>.*?</reasoning>",
    re.DOTALL,
)


def strip_thinking(content: str) -> str:
    """移除 thinking/reasoning 标签。"""
    return _THINKING_PATTERN.sub("", content)


def normalize_whitespace(content: str) -> str:
    """规范化空白：3+ 连续换行合并为 2 个，多个空格合并为 1 个。"""
    content = re.sub(r"\n{3,}", "\n\n", content)
    return re.sub(r"  +", " ", content).strip()


def clean_response(content: str) -> str:
    """清理回复内容：移除 thinking 标签 + 规范空白。"""
    if not content:
        return content
    return normalize_whitespace(strip_thinking(content))


def is_valid_response(content: str, agent_name: str) -> bool:
    """检查响应是否有效（非超时 / 错误占位）。"""
    return not (
        content.startswith(f"[{agent_name} 回应超时") or content.startswith("[错误:")
    )
