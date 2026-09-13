"""Read Codex session JSONL into AI_saver's stable ``Turn`` model.

Codex transcripts are treated as an input adapter, not as durable storage.
The parser keeps only the fields AI_saver needs and the ledger hashes prompt
text before writing it. Unknown records are ignored so a new Codex event does
not break backfill.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .transcript import OTHER, ToolCall, TokenUse, Turn, shell_tool_call

__all__ = ["codex_transcript_root", "read_codex_turns", "read_all_codex_turns"]


def codex_transcript_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def read_all_codex_turns(root: Path | None = None,
                         since: datetime | None = None) -> list[Turn]:
    root = root or codex_transcript_root()
    turns: list[Turn] = []
    for path in sorted(root.rglob("*.jsonl")):
        turns.extend(read_codex_turns(path))
    if since is not None:
        turns = [turn for turn in turns
                 if turn.started_at is not None and turn.started_at >= since]
    turns.sort(key=lambda turn: turn.started_at.timestamp() if turn.started_at else 0.0)
    return turns


def read_codex_turns(path: Path) -> list[Turn]:
    """Parse one Codex transcript without depending on unrelated event fields."""
    session_id = path.stem
    session_cwd = ""
    turns: dict[str, Turn] = {}

    def ensure(turn_id: object, stamp: datetime | None = None) -> Turn | None:
        if not turn_id:
            return None
        key = str(turn_id)
        if key not in turns:
            turns[key] = Turn(prompt_id=key, session_id=session_id,
                              cwd=session_cwd, started_at=stamp, ended_at=stamp)
        elif stamp is not None and turns[key].started_at is None:
            turns[key].started_at = stamp
            turns[key].ended_at = stamp
        return turns[key]

    for entry in _entries(path):
        outer = entry.get("type")
        payload = entry.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        stamp = _stamp(entry)

        if outer == "session_meta":
            session_id = str(payload.get("session_id") or payload.get("id") or session_id)
            session_cwd = str(payload.get("cwd") or session_cwd)
            for turn in turns.values():
                turn.session_id = session_id
                turn.cwd = turn.cwd or session_cwd
            continue

        metadata = payload.get("internal_chat_message_metadata_passthrough")
        metadata = metadata if isinstance(metadata, dict) else {}
        turn = ensure(payload.get("turn_id") or metadata.get("turn_id"), stamp)
        if turn is None:
            continue

        if outer == "turn_context":
            turn.cwd = str(payload.get("cwd") or turn.cwd or session_cwd)
            model = payload.get("model")
            if model and str(model) not in turn.models:
                turn.models = turn.models + (str(model),)
            continue

        if outer == "token_usage_record":
            usage = payload.get("turn_token_usage") or payload.get("usage")
            turn.tokens = _codex_usage(usage)
            continue

        if outer == "response_item" and payload.get("type") == "message" \
                and payload.get("role") == "user" and not turn.prompt:
            turn.prompt = _content_text(payload.get("content"))
            turn.skills_used.update(_explicit_skills(turn.prompt))
            continue

        if outer != "event_msg":
            continue

        event_type = payload.get("type")
        if event_type == "task_started":
            started = _value_stamp(payload.get("started_at")) or stamp
            turn.started_at = started or turn.started_at
        elif event_type in ("task_complete", "turn_aborted"):
            turn.ended_at = _value_stamp(payload.get("completed_at")) or stamp or turn.ended_at
        elif event_type == "item_completed":
            _apply_item(turn, payload.get("item"))
            ended_ms = payload.get("completed_at_ms")
            if isinstance(ended_ms, (int, float)):
                turn.ended_at = datetime.fromtimestamp(ended_ms / 1000, tz=timezone.utc)

    return [turn for turn in turns.values() if turn.prompt]


def _apply_item(turn: Turn, item: object) -> None:
    if not isinstance(item, dict):
        return
    kind = item.get("type")
    if kind == "UserMessage" and not turn.prompt:
        turn.prompt = _content_text(item.get("content"))
        turn.skills_used.update(_explicit_skills(turn.prompt))
    elif kind == "CommandExecution":
        command = item.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        turn.calls.append(shell_tool_call("CommandExecution", str(command or "")))
    elif kind == "Extension":
        target = str(item.get("query") or item.get("action") or "")[:120]
        turn.calls.append(ToolCall("Extension", OTHER, target))


def _codex_usage(value: object) -> TokenUse:
    if not isinstance(value, dict):
        return TokenUse()
    total_input = int(value.get("input_tokens") or 0)
    cache_read = int(value.get("cached_input_tokens") or 0)
    cache_write = int(value.get("cache_write_input_tokens") or 0)
    # Codex input_tokens includes cached input. Split it before applying the
    # lower cache-read weight or cached tokens would be counted twice.
    fresh_input = max(0, total_input - cache_read - cache_write)
    return TokenUse(
        input=fresh_input,
        cache_creation=cache_write,
        cache_read=cache_read,
        output=int(value.get("output_tokens") or 0),
        thinking=int(value.get("reasoning_output_tokens") or 0),
    )


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [str(block.get("text") or "") for block in content
             if isinstance(block, dict) and block.get("type") in ("text", "input_text")]
    return "\n".join(part for part in parts if part).strip()


def _explicit_skills(prompt: str) -> set[str]:
    """Capture stable, explicit Codex skill calls such as ``$focus-file``."""
    return set(re.findall(r"(?<![\w$])\$([a-z0-9]+(?:-[a-z0-9]+)*)", prompt.lower()))


def _entries(path: Path) -> Iterator[dict]:
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _stamp(entry: dict) -> datetime | None:
    return _value_stamp(entry.get("timestamp"))


def _value_stamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(
            value / 1000 if value > 10_000_000_000 else value,
            tz=timezone.utc,
        )
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
