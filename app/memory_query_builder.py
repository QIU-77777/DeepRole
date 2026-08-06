"""Build character retrieval queries from character concern and current input."""

import re
from dataclasses import dataclass

from repository.status_file import extract_status_field
from repository.narrator_output import extract_narrator_output
from repository.agent_files import read_agent_file


@dataclass(frozen=True)
class RetrievalQueries:
    episode: str
    episode_bm25: str
    understanding: str
    understanding_bm25: str


_QUERY_TEXT_LIMIT = 1800
_BM25_TEXT_LIMIT = 700
_UNDERSTANDING_TEXT_LIMIT = 1200


def _clip(text: str, limit: int, *, normalize: bool = True) -> str:
    if normalize:
        text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _get_location(agent_name: str, raw_messages: list[dict] | None) -> str:
    for msg in reversed(raw_messages or []):
        if agent_name != "narrator" and agent_name not in msg.get("visible_to", []):
            continue
        payload = extract_narrator_output(msg)
        if payload:
            return str(payload.get("location") or "").strip()
    return ""


def build_retrieval_queries(
    agent_name: str,
    user_input: str,
    raw_messages: list[dict] | None = None,
) -> RetrievalQueries:
    """Build retrieval queries from character concern and current input.

    episode embedding:  location + user_input
      Anchored to current scene; concern stays out to avoid query drift.
    understanding embedding:  user_input only
      Understandings are not place-bound.
    episode/understanding BM25:  在意的事 + user_input
      Concern provides keyword boosting without distorting embedding direction.
    """
    if agent_name == "narrator":
        concern = ""
    else:
        status = read_agent_file(agent_name, "status.md")
        concern = extract_status_field(status, "在意的事").strip()

    location = _get_location(agent_name, raw_messages)

    episode_query = "\n".join(p for p in [location, user_input] if p)

    if concern:
        bm25_raw = "\n".join(p for p in [concern, user_input] if p)
        bm25_query = _clip(bm25_raw, _BM25_TEXT_LIMIT, normalize=False)
    else:
        bm25_query = _clip(user_input, _BM25_TEXT_LIMIT, normalize=False)

    return RetrievalQueries(
        episode=_clip(episode_query, _QUERY_TEXT_LIMIT) if episode_query else user_input,
        episode_bm25=bm25_query,
        understanding=_clip(user_input, _UNDERSTANDING_TEXT_LIMIT),
        understanding_bm25=bm25_query,
    )
