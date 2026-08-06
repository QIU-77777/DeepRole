"""测试记忆归并输入构造与 raw 对话重标记。"""

import os
import sys
from pathlib import Path

# 设置项目根目录
project_root = Path(__file__).parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

from app.consolidation.inputs import (
    build_episode_closure_payload,
    build_episode_memory_generator_payload,
    build_understanding_patch_payload,
    render_raw_history,
)


def _sample_messages() -> list[dict]:
    return [
        {
            "role": "narrator",
            "content": "**时间**：10月6日 星期五 11:42\n**地点**：舒芙蕾店",
            "visible_to": ["chenxiao", "narrator"],
            "turn": 3,
        },
        {
            "role": "chenxiao",
            "content": "（看着他）……那我点了哦。",
            "visible_to": ["chenxiao", "narrator"],
            "turn": 3,
        },
        {
            "role": "player",
            "content": "我猜啊，你吃不完的是我的",
            "visible_to": ["chenxiao", "narrator"],
            "turn": 4,
        },
        {
            "role": "guyining",
            "content": "先把文件发我。",
            "visible_to": ["chenxiao", "guyining", "narrator"],
            "turn": 5,
        },
        {
            "role": "mitsuki",
            "content": "你们继续。",
            "visible_to": ["mitsuki", "narrator"],
            "turn": 5,
        },
    ]


def test_render_raw_history_filters_by_visibility_and_turn():
    """按 visible_to 过滤 + turn 区间截断，输出带 `[turn=N]` 前缀的对话。"""
    messages = _sample_messages()

    rendered = render_raw_history(messages, visible_to="chenxiao", turn_ge=3, turn_le=4)

    assert "[turn=3]" in rendered
    assert "[turn=4]" in rendered
    assert "[turn=5]" not in rendered  # guyining/mitsuki 回合被截掉
    assert "旁白:" in rendered
    assert "玩家:" in rendered
    assert "mitsuki" not in rendered


def test_render_raw_history_without_filters_renders_all():
    """不带过滤时保留全部消息；无 turn 的消息不加前缀。"""
    messages = [
        {"role": "player", "content": "hello", "visible_to": ["chenxiao"]},
        {"role": "chenxiao", "content": "hi", "visible_to": ["chenxiao"], "turn": 2},
    ]
    rendered = render_raw_history(messages)

    assert "玩家: hello" in rendered
    assert "[turn=2]" in rendered


def test_build_episode_closure_payload_structure():
    """closure payload 只带 recent_history。"""
    payload = build_episode_closure_payload(
        history_transcript="[turn=5] 玩家: 再见",
    )

    assert "<current_turn>" not in payload
    assert "<candidates>" not in payload
    assert "[turn=5] 玩家: 再见" in payload


def test_build_episode_memory_generator_payload_includes_owner_before_memory(monkeypatch):
    """EpisodeMemoryGenerator payload 应先注入记忆主体，再附带 memory 与 raw 对话。"""

    monkeypatch.setattr(
        "app.consolidation.inputs.read_agent_file",
        lambda agent_name, filename: "<role>陈晓</role>" if filename == "soul.md" else "",
    )

    payload = build_episode_memory_generator_payload(
        "chenxiao",
        "## 10月6日\n- **时间**：10月6日 上午\n- **地点**：公司\n- **在场**：我、他\n- **内容**：测试内容。",
        raw_dialogue="旁白：**时间**：10月6日 星期五 11:42",
    )

    assert payload.index("<memory_owner>") < payload.index("<memory_entries>")
    assert "<raw_dialogue>" in payload
    assert "当前整理对象：陈晓（agent_name=chenxiao）" in payload
    assert "- 我 = 陈晓" in payload
    assert "- 他 = 玩家" in payload
    assert "旁白：**时间**：10月6日 星期五 11:42" in payload


def test_build_understanding_patch_payload_includes_entries_and_record():
    payload = build_understanding_patch_payload(
        "[u1] subject='对玩家的认知'\n  content: 旧理解",
        '{"id":"e1","content":"新事件"}',
    )

    assert "<existing_entries>" in payload
    assert "[u1] subject='对玩家的认知'" in payload
    assert "<new_record>" in payload
    assert '"id":"e1"' in payload
    assert "<profile>" not in payload
