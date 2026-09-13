"""The four-option wording, as an editable wiki instead of a code constant.

Interface
---------
    wiki_path(root=None)      -> Path      -- the personal, editable copy
    ensure(root=None)         -> Path      -- seeds it (history-aware) on first use
    load(root=None)           -> dict[str, tuple[Option, ...]]
    reset(root=None)          -> Path      -- overwrite the personal copy back to defaults
    render(table)             -> str       -- table -> markdown, with a table of contents
    parse(text)               -> dict[str, tuple[Option, ...]]
    bootstrap_note(months=3)  -> str       -- what history already shows, for a first-run seed

``gate.assess()`` stays pure -- it never touches disk. This module is where
the disk read lives: a hook calls ``load()`` once per prompt and passes the
result into ``assess(..., options=...)``. Every hook already does I/O (it
reads the profile, appends to the ledger), so this adds nothing new to what
"a hook does", while gate.py itself stays exactly as free of I/O as before.

The point of a *wiki* rather than a second Python constant: a monthly
review can read ``wiki stats`` (which recommendations people actually
followed, where they diverged) and edit the wording directly -- no code
change, no redeploy, and it takes effect on the very next prompt because
every hook invocation is a fresh process that reads the file fresh anyway.

A brand-new install rarely means brand-new data: by the time someone sets
this up, months of transcripts under ``~/.codex/sessions/`` usually
already exist. ``ensure()`` uses that -- day one opens with a documented
summary of real recent habits instead of pretending no data exists.
``bootstrap_note`` only ever adds *facts* (counts, straight from the same
detectors the monthly review uses), never rewritten option wording: this
module runs from a hook, with no model in the loop at all, and rewriting
the actual A/B/C/D text is a judgment call that belongs to the reviewer
(human or model) reading this note -- the same call this project always
keeps separate from mechanical measurement.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence

from .gate import DEFAULT_OPTIONS, Option
from .profile import data_root

__all__ = ["wiki_path", "ensure", "load", "reset", "render", "parse", "bootstrap_note",
          "BOOTSTRAP_MONTHS"]

BOOTSTRAP_MONTHS = 3

_PREAMBLE = (
    "실행 전 확인(게이트)이 보여주는 4지선다 문구입니다.\n\n"
    "월간 리뷰에서 실제로 어떤 추천을 따랐는지, 어디서 어긋났는지(`ai_saver_cli.py "
    "wiki stats`)를 보고 이 파일을 고치세요. 코드를 고칠 필요가 없습니다 -- 다음 "
    "프롬프트부터 바로 이 문구가 쓰입니다. `wiki reset`으로 언제든 원래대로 "
    "되돌릴 수 있습니다.\n"
)

_ROW = re.compile(r"^\|\s*([A-Za-z가-힣0-9]+)\s*\|\s*(.+?)\s*\|\s*(★{1,10})\s*\|\s*(.+?)\s*\|\s*$")
_SEP = re.compile(r"^\|[\s:|-]+\|$")
_SECTION = re.compile(r"^##\s+(?!목차)(\S+)", re.M)


def wiki_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "wiki" / "options.md"


def ensure(root: Path | None = None, transcripts_root: Path | None = None) -> Path:
    """The personal copy, creating it from DEFAULT_OPTIONS -- opened with a
    ``bootstrap_note()`` of what recent history already shows, when there
    is any -- if this is the first time anything has asked for it.

    ``transcripts_root`` exists so tests (and only tests) can point the
    bootstrap scan at an empty directory instead of this machine's real
    Codex history -- production code never passes it.
    """
    path = wiki_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        text = render(DEFAULT_OPTIONS)
        note = bootstrap_note(transcripts_root=transcripts_root)
        if note:
            text = text.replace("## 목차", note + "## 목차", 1)
        path.write_text(text, encoding="utf-8")
    return path


def load(root: Path | None = None, transcripts_root: Path | None = None) -> dict[str, tuple[Option, ...]]:
    """Never raises, never returns an incomplete table: a task whose section
    is missing, unparseable, or missing one of its four option keys (a
    single hand-edited row broken by a typo, say) falls back to
    DEFAULT_OPTIONS for that task alone -- never a three-option "four-option
    gate", and never the whole table just because one section had a typo."""
    path = ensure(root, transcripts_root)
    try:
        parsed = parse(path.read_text(encoding="utf-8"))
    except OSError:
        parsed = {}

    table: dict[str, tuple[Option, ...]] = {}
    for task, default in DEFAULT_OPTIONS.items():
        candidate = parsed.get(task)
        wanted_keys = {option.key for option in default}
        if candidate and {option.key for option in candidate} == wanted_keys:
            table[task] = candidate
        else:
            table[task] = default
    return table


def reset(root: Path | None = None) -> Path:
    path = wiki_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(DEFAULT_OPTIONS), encoding="utf-8")
    return path


def render(table: Mapping[str, Sequence[Option]]) -> str:
    lines = ["# AI_saver 옵션 위키", "", _PREAMBLE, "## 목차"]
    for task in table:
        lines.append(f"- [{task}](#{task})")
    lines.append("")
    for task, options in table.items():
        lines += [f"## {task}", "", "| 키 | 라벨 | 별점 | 규칙 |", "|---|---|---|---|"]
        for option in options:
            lines.append(f"| {option.key} | {option.label} | {option.stars} | {option.rule} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def bootstrap_note(months: int = BOOTSTRAP_MONTHS, transcripts_root: Path | None = None) -> str:
    """The top real habits found in the last ``months`` of local
    transcripts, as a markdown section -- or "" when there is nothing to
    say (a genuinely new machine with no history, or the analysis fails
    for any reason at all). Never raises: this runs from ``ensure()``,
    which a hook calls on every prompt, and seeding a file must never be
    allowed to break someone's turn.

    ``transcripts_root`` defaults to the real ``~/.codex/sessions/``.
    Passing a directory explicitly keeps the legacy Claude transcript
    adapter available for tests and migrations.
    """
    try:
        from collections import Counter
        from datetime import datetime, timedelta, timezone

        from .signals import CODES, detect

        since = datetime.now(timezone.utc) - timedelta(days=30 * months)
        if transcripts_root is None:
            from .codex_transcript import read_all_codex_turns
            turns = read_all_codex_turns(since=since)
        else:
            from .transcript import read_all_turns
            turns = read_all_turns(transcripts_root, since=since)
        if not turns:
            return ""
        counts = Counter(f.code for f in detect(turns))
        top = counts.most_common(3)
        if not top:
            return ""
    except Exception:
        return ""

    lines = [
        "## 설치 전 데이터로 만든 초기 상태",
        "",
        f"빈 기본값이 아니라, 최근 {months}개월 실제 작업 기록을 보고 시작합니다.",
        "",
    ]
    lines += [f"- {CODES.get(code, code)} — {count}회" for code, count in top]
    lines += [
        "",
        "실행 전 확인(게이트) 판정 이력(`ai_saver_cli.py wiki stats`)은 아직 없습니다 -- "
        "게이트를 실시간으로 켜고 판정이 쌓이면 월간 리뷰가 이어서 채웁니다. 지금은 실제 "
        "작업 습관만 반영했고, 옵션 문구 자체는 아직 손대지 않았습니다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def parse(text: str) -> dict[str, tuple[Option, ...]]:
    """Ignores anything it doesn't recognise (the preamble, the 목차 list,
    stray prose someone left in a section) rather than raising -- a person
    editing this file by hand should never be able to break the gate."""
    table: dict[str, tuple[Option, ...]] = {}
    parts = _SECTION.split(text)
    for i in range(1, len(parts), 2):
        task = parts[i].strip().lower()
        rows = []
        for line in parts[i + 1].splitlines():
            line = line.strip()
            if not line or _SEP.match(line):
                continue
            match = _ROW.match(line)
            if match:
                key, label, stars, rule = match.groups()
                rows.append(Option(key, label, stars, rule))
        if rows:
            table[task] = tuple(rows)
    return table
