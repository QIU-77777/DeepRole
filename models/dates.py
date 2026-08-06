"""游戏日期工具：中文日期解析 / day-of-year / 天数差。纯函数，无 I/O。"""

import re
from datetime import datetime


def parse_cn_date(date_text: str) -> tuple[int, int] | None:
    """解析中文日期格式（如 4月3日 / 4月3日 08:00）为元组 (月, 日)。"""
    m = re.search(r"(\d{1,2})月(\d{1,2})日", (date_text or "").strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        datetime(2000, month, day)
    except ValueError:
        return None
    return month, day


def canonical_cn_date(date_text: str) -> str | None:
    """将中文日期文本规范化为 X月X日。"""
    parsed = parse_cn_date(date_text)
    if parsed is None:
        return None
    month, day = parsed
    return f"{month}月{day}日"


def game_day_number(date_text: str) -> int | None:
    """将游戏日期映射到固定年份中的 day-of-year，便于比较天数差。"""
    parsed = parse_cn_date(date_text)
    if parsed is None:
        return None
    month, day = parsed
    return datetime(2000, month, day).timetuple().tm_yday


def game_day_diff(current_date: str, past_date: str) -> int | None:
    """返回两个游戏日期的天数差；若无法解析返回 None。"""
    current_day = game_day_number(current_date)
    past_day = game_day_number(past_date)
    if current_day is None or past_day is None:
        return None
    return max(current_day - past_day, 0)
