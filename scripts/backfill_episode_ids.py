"""One-time script: assign stable UUIDs to existing memory.jsonl entries that have no id."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from models import EpisodeMemory
from repository.memory_store import memory_jsonl_path
from repository.config import get_agent_names


def backfill(agent_name: str) -> int:
    path = memory_jsonl_path(agent_name)
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            updated.append(line)
            continue
        try:
            ep = EpisodeMemory.model_validate_json(stripped)
        except ValidationError:
            updated.append(line)
            continue
        if not ep.id:
            ep = ep.model_copy(update={"id": uuid.uuid4().hex})
            count += 1
        updated.append(ep.model_dump_json())
    if count > 0:
        path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return count


if __name__ == "__main__":
    for name in get_agent_names(include_narrator=False):
        n = backfill(name)
        print(f"{name}: {n} records backfilled")
