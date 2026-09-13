"""Reading Claude Code transcripts.

The only module that knows what a Claude Code JSONL file looks like.

Interface
---------
    read_turns(path)          -> list[Turn]
    read_all_turns(root, ...) -> list[Turn]
    transcript_root()         -> Path

A ``Turn`` is one user prompt plus everything the agent did in response.
Everything below that line -- heterogeneous entry types, usage records that
are duplicated across every content block of one API request, tool inputs
whose shape differs per tool, shell commands that are really file reads --
is implementation detail and must not leak to callers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

__all__ = ["TokenUse", "ToolCall", "Turn", "read_turns", "read_all_turns", "transcript_root"]

# Billing-proportional weights. Cache writes cost more than fresh input,
# cache reads cost far less. Keeping the formula here means every caller
# -- signals, report, ledger -- agrees on what "cost" means.
_CACHE_WRITE_WEIGHT = 1.25
_CACHE_READ_WEIGHT = 0.10


@dataclass(frozen=True)
class TokenUse:
    """Token consumption of one or more API requests."""

    input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0
    thinking: int = 0

    @property
    def weighted(self) -> float:
        """Cost in billing-proportional units."""
        return (
            self.input
            + self.cache_creation * _CACHE_WRITE_WEIGHT
            + self.cache_read * _CACHE_READ_WEIGHT
            + self.output
        )

    @property
    def raw(self) -> int:
        return self.input + self.cache_creation + self.cache_read + self.output

    def __add__(self, other: "TokenUse") -> "TokenUse":
        return TokenUse(
            self.input + other.input,
            self.cache_creation + other.cache_creation,
            self.cache_read + other.cache_read,
            self.output + other.output,
            self.thinking + other.thinking,
        )


# Tool kinds. Callers reason about kinds, never about tool names.
READ, EDIT, BUILD, SEARCH, OTHER = "read", "edit", "build", "search", "other"


@dataclass(frozen=True)
class ToolCall:
    name: str
    kind: str
    target: str  # file path, command signature, or query -- normalised for comparison


@dataclass
class Turn:
    """One user prompt and everything that followed it."""

    prompt_id: str = ""
    session_id: str = ""
    cwd: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    prompt: str = ""
    models: tuple[str, ...] = ()
    tokens: TokenUse = field(default_factory=TokenUse)
    calls: list[ToolCall] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)  # options picked when the gate asked
    skills_used: set[str] = field(default_factory=set)  # from assistant entries' attributionSkill

    @property
    def cost(self) -> float:
        return self.tokens.weighted

    @property
    def seconds(self) -> float:
        if not self.started_at or not self.ended_at:
            return 0.0
        return (self.ended_at - self.started_at).total_seconds()

    def targets(self, kind: str) -> list[str]:
        return [c.target for c in self.calls if c.kind == kind and c.target]

    @property
    def month(self) -> str:
        return self.started_at.strftime("%Y-%m") if self.started_at else "unknown"


def transcript_root() -> Path:
    return Path.home() / ".claude" / "projects"


def read_all_turns(root: Path | None = None, since: datetime | None = None) -> list[Turn]:
    """Every turn in every transcript under ``root``, oldest first."""
    root = root or transcript_root()
    turns: list[Turn] = []
    for path in sorted(root.rglob("*.jsonl")):
        turns.extend(read_turns(path))
    if since is not None:
        turns = [t for t in turns if t.started_at and t.started_at >= since]
    turns.sort(key=lambda t: t.started_at or datetime.min.replace(tzinfo=None))
    return turns


def read_turns(path: Path) -> list[Turn]:
    """Parse one transcript file into turns.

    Tolerates truncated and malformed lines: a transcript is written while
    the session runs, so the last line may be half-flushed.
    """
    turns: list[Turn] = []
    current: Turn | None = None
    counted_requests: set[str] = set()
    question_ids: set[str] = set()

    for entry in _entries(path):
        kind = entry.get("type")

        prompt = _prompt_text(entry) if kind == "user" else None
        if kind == "user" and prompt is None:
            if current is not None:
                current.choices.extend(_choices(entry, question_ids))
            continue
        if prompt is not None:
            current = Turn(
                prompt_id=entry.get("promptId") or entry.get("uuid") or "",
                session_id=entry.get("sessionId") or path.stem,
                cwd=entry.get("cwd") or "",
                started_at=_stamp(entry),
                ended_at=_stamp(entry),
                prompt=prompt,
            )
            turns.append(current)
            continue

        if kind != "assistant":
            continue
        if current is None:
            # Transcript resumed mid-conversation; keep the cost, lose the prompt.
            current = Turn(session_id=entry.get("sessionId") or path.stem,
                           cwd=entry.get("cwd") or "",
                           started_at=_stamp(entry))
            turns.append(current)

        stamp = _stamp(entry)
        if stamp:
            current.ended_at = stamp

        message = entry.get("message") or {}

        # Claude Code stamps which skill was active on the assistant entry
        # itself -- the direct "was /command actually used" signal, not
        # something inferred from the prompt text.
        skill = entry.get("attributionSkill")
        if skill:
            current.skills_used.add(str(skill))

        # One API response is split across several transcript lines (thinking,
        # text, each tool_use) and EVERY line repeats the same usage record.
        # Counting per line inflates cost ~2.4x on real sessions.
        request_id = entry.get("requestId")
        if request_id and request_id not in counted_requests:
            counted_requests.add(request_id)
            current.tokens = current.tokens + _usage(message.get("usage"))
            model = message.get("model")
            if model and model not in current.models:
                current.models = current.models + (model,)

        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                current.calls.append(_tool_call(block))
                if str(block.get("name") or "").endswith("AskUserQuestion") and block.get("id"):
                    question_ids.add(str(block["id"]))

    return turns


# --- implementation ------------------------------------------------------


def _entries(path: Path) -> Iterator[dict]:
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _stamp(entry: dict) -> datetime | None:
    raw = entry.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _usage(usage: object) -> TokenUse:
    if not isinstance(usage, dict):
        return TokenUse()
    details = usage.get("output_tokens_details")
    thinking = details.get("thinking_tokens", 0) if isinstance(details, dict) else 0
    return TokenUse(
        input=int(usage.get("input_tokens") or 0),
        cache_creation=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read=int(usage.get("cache_read_input_tokens") or 0),
        output=int(usage.get("output_tokens") or 0),
        thinking=int(thinking or 0),
    )


_NOISE = re.compile(
    r"<system-reminder>.*?</system-reminder>"
    r"|<command-(name|message|args)>.*?</command-\1>"
    r"|<local-command-stdout>.*?</local-command-stdout>",
    re.DOTALL,
)


def _prompt_text(entry: dict) -> str | None:
    """Return the user's own words, or None if this is not a real prompt.

    Most ``user`` entries are tool results being fed back to the model, not
    something a person typed.
    """
    if entry.get("toolUseResult") is not None:
        return None
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isVisibleInTranscriptOnly"):
        return None
    content = (entry.get("message") or {}).get("content")
    text = _text_of(content)
    text = _NOISE.sub("", text).strip()
    return text or None


_CHOICE = re.compile(r"\b([A-D])\s*[.)．]")


def _choices(entry: dict, question_ids: set[str]) -> list[str]:
    """Best-effort: which option the person picked when the gate asked.

    Feeds the UNDERSCOPED_FAIL detector. Best-effort on purpose -- a missed
    answer costs one unmeasured data point, never a wrong record.
    """
    if not question_ids:
        return []
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    picked: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if str(block.get("tool_use_id") or "") not in question_ids:
            continue
        match = _CHOICE.search(_result_text(block) or _result_text(entry.get("toolUseResult")))
        if match:
            picked.append(match.group(1))
    return picked


def _result_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _text_of(value.get("content")) or json.dumps(value, ensure_ascii=False)[:400]
    if isinstance(value, list):
        return _text_of(value)
    return ""


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(parts)


_READ_TOOLS = {"read", "notebookread"}
_EDIT_TOOLS = {"edit", "write", "notebookedit", "multiedit"}
_SEARCH_TOOLS = {"grep", "glob", "websearch", "webfetch", "tool_search", "toolsearch"}

_BASH_READ = re.compile(r"^\s*(cat|head|tail|less|more|sed\s+-n|grep|rg|ls|dir|find|wc|stat|type)\b")
_BASH_EDIT = re.compile(r"\bsed\s+-i\b|\b(cp|mv|rm|mkdir|touch|tee)\b|>>?\s*[^\s|&]")
_BASH_BUILD = re.compile(
    r"\b(npm|yarn|pnpm|bun)\s+(run\s+)?(build|dev|start|test|lint)\b"
    r"|\b(pytest|tox|jest|vitest|cargo|gradle|mvn|make|tsc|vite|webpack|nox|unittest)\b"
    r"|\bgo\s+(test|build)\b|\bdotnet\s+(build|test)\b|\bpython\s+-m\s+(pytest|unittest)\b"
)
_PATHISH = re.compile(r"[^\s'\"|;&><]*[\\/][^\s'\"|;&><]*|[\w.-]+\.[A-Za-z0-9]{1,5}")


def _tool_call(block: dict) -> ToolCall:
    name = str(block.get("name") or "")
    args = block.get("input")
    args = args if isinstance(args, dict) else {}
    key = name.lower().rsplit("__", 1)[-1]

    if key == "bash" or key == "powershell":
        return _shell_call(name, str(args.get("command") or ""))

    for field_name in ("file_path", "path", "notebook_path"):
        if args.get(field_name):
            target = _normalise(str(args[field_name]))
            kind = EDIT if key in _EDIT_TOOLS else READ if key in _READ_TOOLS else OTHER
            return ToolCall(name, kind, target)

    if key in _SEARCH_TOOLS:
        target = _normalise(str(args.get("pattern") or args.get("query") or args.get("url") or ""))
        return ToolCall(name, SEARCH, target)
    if key in _EDIT_TOOLS:
        return ToolCall(name, EDIT, "")
    if key in _READ_TOOLS:
        return ToolCall(name, READ, "")
    return ToolCall(name, OTHER, "")


def _shell_call(name: str, command: str) -> ToolCall:
    """Classify a shell command. Heavy users do most file work through the shell.

    Classification and identity are both taken from the same core segment, so
    ``cd app && npm test`` is the same command as ``npm test``, and a build in
    someone else's ``cd`` prefix is not attributed to this one.
    """
    core = _core(command)
    if _BASH_BUILD.search(core):
        return ToolCall(name, BUILD, core[:60])
    if _BASH_EDIT.search(core):
        return ToolCall(name, EDIT, _first_path(core) or core[:60])
    if _BASH_READ.match(core):
        return ToolCall(name, READ, _first_path(core) or core[:60])
    return ToolCall(name, OTHER, core[:60])


_PREAMBLE = re.compile(r"^\s*(cd\b[^&;|]*|export\s+\w+=\S*|\w+=\S*)\s*$", re.I)


def _core(command: str) -> str:
    """The part of a shell command that says what was actually run.

    Real commands arrive wrapped in ``cd ... && export X=1 && <the actual
    thing>``, and the wrapper differs run to run. Keying on the raw string
    would both miss repeats and fill the report with path noise.
    """
    segments = [s for s in re.split(r"&&|\|\||;|\n", command) if s.strip()]
    meaningful = [s for s in segments if not _PREAMBLE.match(s)] or segments
    collapsed = re.sub(r"\s+", " ", meaningful[-1].strip().strip("\"'"))
    collapsed = re.sub(r"^(?:\w+=\S*\s+)+", "", collapsed)  # inline env assignments
    # Only the program's own path is noise; argument paths identify the target.
    collapsed = re.sub(r"^[\"']?[^\s\"']*[\\/]([\w.-]+)[\"']?", r"\1", collapsed, count=1)
    return collapsed.lower()


def _first_path(command: str) -> str:
    for token in command.split():
        if token.startswith("-"):
            continue
        match = _PATHISH.fullmatch(token.strip("'\"`"))
        if match:
            return _normalise(match.group(0))
    return ""


def _normalise(target: str) -> str:
    return target.replace("\\", "/").strip().lower()
