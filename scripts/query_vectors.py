#!/usr/bin/env python3
"""向量库查询脚本（memory-only schema）

Usage:
    # 总览
    uv run python scripts/query_vectors.py list

    # 查看内容（可按角色或日期过滤）
    uv run python scripts/query_vectors.py show [--limit 20] [--agent chenxiao] [--date 10月4日] [--order desc]

    # 统计信息
    uv run python scripts/query_vectors.py stats
"""

import argparse
import asyncio
import sys
from pathlib import Path

import aiosqlite

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from repository.vector_store import DB_PATH


async def _open_db() -> aiosqlite.Connection | None:
    if not Path(DB_PATH).exists():
        return None
    return await aiosqlite.connect(DB_PATH)


async def cmd_list():
    """显示全库总览：总条数、覆盖日期等"""
    db = await _open_db()
    if db is None:
        print("总条数: 0")
        print("覆盖日期(游戏内): 0 天")
        return
    try:
        row = await (await db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT game_date) FROM EpisodeMemory"
        )).fetchone()
        total, days = row if row else (0, 0)

        print(f"总条数: {total}")
        print(f"覆盖日期(游戏内): {days} 天")

        # 按游戏日期分布（Top 10）
        cur = await db.execute(
            "SELECT game_date, COUNT(*) AS c FROM EpisodeMemory GROUP BY game_date ORDER BY c DESC LIMIT 10"
        )
        rows = await cur.fetchall()
        if rows:
            print("\nTop 日期(按条数):")
            for d, c in rows:
                print(f"  {d or '-'}: {c}")
    finally:
        await db.close()


async def cmd_show(limit: int, agent: str | None, date: str | None, order: str):
    """查看 EpisodeMemory 内容，支持按角色/日期过滤"""
    db = await _open_db()
    if db is None:
        print("=== EpisodeMemory（显示 0/0）===")
        return
    try:
        where = []
        params: list = []
        if agent:
            where.append("memory_owner = ?")
            params.append(agent)
        if date:
            where.append("game_date = ?")
            params.append(date)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        order_sql = "DESC" if order.lower().startswith("d") else "ASC"

        sql = (
            "SELECT id, memory_owner, game_date, title, content, last_recalled_at "
            "FROM EpisodeMemory" + where_sql + f" ORDER BY id {order_sql} LIMIT ?"
        )
        params2 = params + [limit]
        cur = await db.execute(sql, params2)
        rows = await cur.fetchall()

        # 总数
        sql_cnt = "SELECT COUNT(*) FROM EpisodeMemory" + where_sql
        total = (await (await db.execute(sql_cnt, params)).fetchone())[0]

        print(f"=== EpisodeMemory（显示 {len(rows)}/{total}）===\n")
        for row_id, owner, game_date, title, content, recalled_at in rows:
            preview = content[:200].replace("\n", "\\n")
            if len(content) > 200:
                preview += "…"
            print(
                f"id={row_id}, owner={owner}, game_date={game_date or '-'}, "
                f"title={title or '-'}, recalled_at={recalled_at or '-'}"
            )
            print(f"{len(content)} chars: {preview}\n")
    finally:
        await db.close()


async def cmd_stats():
    """显示整体统计（长度分布、日期分布 Top）"""
    db = await _open_db()
    if db is None:
        print("总计: 0 条记忆")
        print("（空库）")
        return
    try:
        total_chunks = (await (await db.execute("SELECT COUNT(*) FROM EpisodeMemory")).fetchone())[0]
        lens = await (
            await db.execute(
                "SELECT SUM(LENGTH(content)), MIN(LENGTH(content)), MAX(LENGTH(content)), AVG(LENGTH(content)) FROM EpisodeMemory"
            )
        ).fetchone()
        total_len, min_len, max_len, avg_len = lens if lens else (0, 0, 0, 0)

        print(f"总计: {total_chunks} 条记忆")
        if total_chunks > 0:
            print(f"字符总数: {total_len}, 最短: {min_len}, 最长: {max_len}, 平均: {avg_len:.0f}")
        else:
            print("（空库）")

        cur = await db.execute(
            "SELECT game_date, COUNT(*) AS c FROM EpisodeMemory GROUP BY game_date ORDER BY c DESC LIMIT 10"
        )
        rows = await cur.fetchall()
        if rows:
            print("\nTop 日期(按条数):")
            for d, c in rows:
                print(f"  {d or '-'}: {c}")
    finally:
        await db.close()


async def main():
    parser = argparse.ArgumentParser(description="查询记忆向量数据库")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="显示全库总览")
    sub.add_parser("stats", help="显示整体统计")

    show_parser = sub.add_parser("show", help="查看记忆内容")
    show_parser.add_argument("--limit", type=int, default=20, help="显示条数（默认 20）")
    show_parser.add_argument("--agent", type=str, default=None, help="按角色过滤（如 chenxiao）")
    show_parser.add_argument("--date", type=str, default=None, help="按游戏日期过滤（如 10月4日）")
    show_parser.add_argument("--order", type=str, default="asc", help="排序：asc|desc（默认 asc）")

    args = parser.parse_args()

    if args.command == "list":
        await cmd_list()
    elif args.command == "show":
        await cmd_show(args.limit, args.agent, args.date, args.order)
    elif args.command == "stats":
        await cmd_stats()


if __name__ == "__main__":
    asyncio.run(main())
