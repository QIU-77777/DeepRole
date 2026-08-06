"""Helpers for structured narrator output stored in raw history."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


NARRATOR_OUTPUT_FIELDS = (
    "targets",
    "date",
    "time",
    "location",
    "present_characters",
    "scene_description",
    "new_characters",
)


def extract_narrator_output(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return a narrator output payload from a raw message, or an empty dict."""
    if message.get("role") != "narrator":
        return {}

    nested = message.get("narrator_output")
    if isinstance(nested, Mapping):
        return _clean_payload(nested)

    if any(field in message for field in NARRATOR_OUTPUT_FIELDS):
        return _clean_payload(message)

    return {}


def raw_message_text(message: Mapping[str, Any]) -> str:
    """Return searchable/prompt text without converting narrator JSON to markdown."""
    payload = extract_narrator_output(message)
    if payload:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return str(message.get("content") or "").strip()


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = {field: payload.get(field) for field in NARRATOR_OUTPUT_FIELDS if field in payload}
    cleaned.setdefault("targets", [])
    cleaned.setdefault("new_characters", [])
    return cleaned


def role_to_speaker(role: str) -> str:
    """将消息 role 映射到显示名称，用于历史摘要。"""
    if role == "player":
        return "玩家"
    if role == "narrator":
        return "旁白"
    return role
