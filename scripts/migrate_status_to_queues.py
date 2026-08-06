"""一次性迁移：把 status.md 里的「打算」/「待触发事件」段抽成 JSON 队列文件。

背景：打算 / 待触发事件 原先以 markdown ## 段存于 status.md，靠正则增删；现改为
类型化列表（intents.json / pending_events.json，见 storage.intent_queue）。本脚本：

- 角色 status.md：把「打算」段的 `- [ ] 【…】…` 抽进同目录 intents.json，并删除该段；
- 旁白 status.md：把「待触发事件」段抽进 pending_events.json，删除该段；
  并删除「和玩家的关系」段（已改为 prompt 渲染时现算，不再落盘）。

幂等：段已不存在则跳过。用法：

    uv run python scripts/migrate_status_to_queues.py            # 迁移 templates + runtime
    uv run python scripts/migrate_status_to_queues.py --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PLACEHOLDER = "（暂无）"


def _parse_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """返回 (标题行, [(段名, 段体), ...])，保持原顺序。"""
    title = ""
    sections: list[tuple[str, str]] = []
    cur_name: str | None = None
    cur_lines: list[str] = []
    for line in text.split("\n"):
        if line.startswith("# ") and title == "" and cur_name is None:
            title = line
            continue
        if line.startswith("## "):
            if cur_name is not None:
                sections.append((cur_name, "\n".join(cur_lines).strip()))
            cur_name = line[3:].strip()
            cur_lines = []
        elif cur_name is not None:
            cur_lines.append(line)
    if cur_name is not None:
        sections.append((cur_name, "\n".join(cur_lines).strip()))
    return title or "# 我的状态", sections


def _items_from_body(body: str) -> list[str]:
    """把段体的列表行转成队列条目（剥掉 `- [ ] ` / `- ` 前缀，跳过占位符）。"""
    items: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if not s or s == _PLACEHOLDER:
            continue
        for prefix in ("- [ ] ", "- [x] ", "- "):
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
                break
        if s:
            items.append(s)
    return items


def migrate_status_file(status_path: Path, *, is_narrator: bool, dry_run: bool) -> str | None:
    """迁移单个 status.md；返回一行说明，无改动返回 None。"""
    if not status_path.exists():
        return None
    title, sections = _parse_sections(status_path.read_text(encoding="utf-8"))

    queue_section = "待触发事件" if is_narrator else "打算"
    queue_file = "pending_events.json" if is_narrator else "intents.json"
    drop = {queue_section}
    if is_narrator:
        drop.add("和玩家的关系")  # 派生字段，改为现算

    if not any(name in drop for name, _ in sections):
        return None

    items: list[str] = []
    for name, body in sections:
        if name == queue_section:
            items = _items_from_body(body)

    kept = [(name, body) for name, body in sections if name not in drop]
    new_text = title + "\n\n" + "".join(
        f"## {name}\n{body}\n\n" for name, body in kept
    )

    queue_path = status_path.parent / queue_file
    if dry_run:
        return f"[dry-run] {status_path}: {queue_section}×{len(items)} → {queue_file}"

    queue_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    status_path.write_text(new_text, encoding="utf-8")
    return f"{status_path}: {queue_section}×{len(items)} → {queue_file}"


def _iter_agent_status(root: Path):
    """产出 (status_path, is_narrator)。root 下每个含 status.md 的子目录视为一个 agent。"""
    for status_path in sorted(root.rglob("status.md")):
        yield status_path, status_path.parent.name == "narrator"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    roots = [_ROOT / "data" / "templates", _ROOT / "data" / "runtime" / "characters"]
    changed = 0
    for root in roots:
        if not root.exists():
            continue
        for status_path, is_narrator in _iter_agent_status(root):
            msg = migrate_status_file(status_path, is_narrator=is_narrator, dry_run=args.dry_run)
            if msg:
                changed += 1
                print(msg)
    print(f"\n{'(dry-run) ' if args.dry_run else ''}migrated {changed} status.md")


if __name__ == "__main__":
    main()
