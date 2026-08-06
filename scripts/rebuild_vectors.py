#!/usr/bin/env python3
"""向量库重建脚本 - 重建角色的长期记忆向量数据库

Usage:
    uv run python scripts/rebuild_vectors.py             # 重建所有角色
    uv run python scripts/rebuild_vectors.py chenxiao   # 重建指定角色
"""

import asyncio
import sys
from pathlib import Path

# 将项目根目录加入路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 必须先加载环境变量，再导入 vector_store
from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from app.memory.indexer import rebuild_memory_index
from repository.vector_store import vector_store


async def main() -> None:
    agent_name = sys.argv[1] if len(sys.argv) > 1 else None
    if agent_name:
        print(f"[向量重建] 重建角色: {agent_name}", flush=True)
    else:
        print("[向量重建] 重建所有角色", flush=True)
    try:
        await rebuild_memory_index(agent_name)
        print("[向量重建] 完成", flush=True)
    finally:
        await vector_store.close()


if __name__ == "__main__":
    asyncio.run(main())
