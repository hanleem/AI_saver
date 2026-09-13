"""Turning a repeated habit into a slash command that actually exists.

Interface
---------
    purpose_of(code)              -> Purpose | None
    render_skill(codes, counts)   -> Skill
    Skill.write(root=None)        -> Path
    skills_root()                 -> Path

A monthly report naming ``/focus-file`` is a proposal, not a command --
Claude Code only shows it in ``/`` autocomplete once a real
``SKILL.md`` exists under a skills folder it watches. This module is the
only place that knows what that file has to look like, so the report can
stay a single line ("promote this") and this module supplies both the
plain-language purpose (why the report should explain it in the same
breath) and the actual file.

One code can share a command with another (two different symptoms, same
fix), so ``render_skill`` takes a set of codes and merges their rules
rather than producing one file per code -- the SkillReducer lesson this
project keeps citing: every extra skill is a standing cost, so a shared
remedy should be one file, not several.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

__all__ = ["Purpose", "Skill", "PURPOSE", "purpose_of", "render_skill", "skills_root"]

DESCRIPTION_LIMIT = 500
BODY_LINE_LIMIT = 60


@dataclass(frozen=True)
class Purpose:
    command: str  # the /name it becomes
    summary: str  # one line: what this actually does, for the report
    rules: tuple[str, ...]  # the behaviour, once promoted


# One entry per detector in signals.CODES. Two codes may point at the same
# command (SCOPE_BLOWUP and VAGUE_SCOPE are both "the ask was too open");
# render_skill merges their rules instead of writing two files.
PURPOSE: dict[str, Purpose] = {
    "REDISCOVERY": Purpose(
        "focus-file",
        "지목한 파일만 읽고, 그 파일을 다시 열지 않는다.",
        (
            "사용자가 지목한 파일만 연다. 열기 전에 전체를 한 번에 읽어 내용을 기억한다.",
            "같은 파일을 이미 읽었으면 다시 열지 않는다 -- 기억한 내용을 그대로 쓴다.",
            "사용자가 새로 지목하기 전까지 다른 파일은 열지 않는다.",
        ),
    ),
    "SCOPE_BLOWUP": Purpose(
        "scoped-edit",
        "탐색 범위를 사용자가 말한 곳으로 좁힌다.",
        (
            "사용자가 지목한 파일·화면만 수정 대상으로 삼는다.",
            "관련 여부가 불확실한 파일은 열기 전에 먼저 사용자에게 확인한다.",
            "프로젝트 전체 탐색은 사용자가 명시적으로 요청했을 때만 한다.",
        ),
    ),
    "BUILD_LOOP": Purpose(
        "quick-test",
        "build·test를 마지막에 정확히 한 번만 돌린다.",
        (
            "수정하는 동안에는 build나 test를 실행하지 않는다.",
            "요청한 수정이 모두 끝난 뒤, 확인을 위해 정확히 한 번만 실행한다.",
            "실패하면 원인을 고치고 한 번 더 실행한다 -- 그 이상 반복하지 않는다.",
        ),
    ),
    "THRASH": Purpose(
        "ui-edit",
        "원하는 결과를 먼저 못박고, 고친 뒤 되돌리지 않는다.",
        (
            "시작 전 사용자가 원하는 최종 결과를 한 줄로 요약해 확인한다.",
            "그 결과에 필요한 부분만 고치고, 관련 없는 다른 부분은 손대지 않는다.",
            "다 고친 뒤 한 번만 확인하고 끝낸다 -- 고쳤다 되돌리는 것을 반복하지 않는다.",
        ),
    ),
    "CONTEXT_REPEAT": Purpose(
        "project-brief",
        "프로젝트 설명을 매번 다시 안 쓰도록 CLAUDE.md를 먼저 읽는다.",
        (
            "작업 시작 전 프로젝트 루트의 CLAUDE.md(없으면 README.md)를 먼저 읽는다.",
            "거기 적힌 내용은 사용자가 다시 말하지 않아도 되므로 다시 묻지 않는다.",
            "CLAUDE.md가 없는데 사용자가 프로젝트를 길게 설명했다면, 다음에 또 "
            "설명할 필요 없도록 CLAUDE.md를 새로 만들지 물어본다.",
        ),
    ),
    "VAGUE_SCOPE": Purpose(
        "scoped-edit",
        "대상이 없는 요청은 시작 전에 먼저 되묻는다.",
        (
            "요청에 대상(파일·화면·기능)이 없으면, 도구를 쓰기 전에 무엇을 고칠지 먼저 물어본다.",
            "'전체', '다', '알아서' 같은 말이 나오면 구체적인 범위로 되물은 뒤 시작한다.",
        ),
    ),
    "UNDERSCOPED_FAIL": Purpose(
        "wider-edit",
        "이런 작업은 한 파일로 좁히지 않고 관련 파일까지 함께 본다.",
        (
            "이 종류의 작업은 관련 파일 1~2개를 함께 확인하는 것부터 시작한다.",
            "고친 뒤 관련 화면·기능이 함께 깨지지 않았는지 한 번 더 확인한다.",
        ),
    ),
}


def purpose_of(code: str) -> Purpose | None:
    return PURPOSE.get(code)


@dataclass(frozen=True)
class Skill:
    command: str
    summary: str
    rules: tuple[str, ...]
    sources: tuple[str, ...]  # which detector codes fed this file
    occurrences: int

    def render(self) -> str:
        body = [
            "---",
            f"name: {self.command}",
            f"description: {self.summary}",
            "---",
            "",
            f"AI_saver가 지난 기록에서 같은 습관을 {self.occurrences}번 감지해 만들었습니다.",
            "",
        ]
        body += [f"- {rule}" for rule in self.rules]
        text = "\n".join(body) + "\n"
        lines = text.count("\n")
        if lines > BODY_LINE_LIMIT:
            raise ValueError(f"/{self.command}: 본문 {lines}줄 > 예산 {BODY_LINE_LIMIT}줄")
        if len(self.summary) > DESCRIPTION_LIMIT:
            raise ValueError(f"/{self.command}: description {len(self.summary)}자 > 예산 {DESCRIPTION_LIMIT}자")
        return text

    def write(self, root: Path | None = None) -> Path:
        folder = (root or skills_root()) / self.command
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "SKILL.md"
        path.write_text(self.render(), encoding="utf-8")
        return path


def skills_root() -> Path:
    """Personal skills folder Claude Code watches for live autocomplete."""
    return Path.home() / ".claude" / "skills"


def render_skill(codes: Sequence[str], counts: dict[str, int]) -> Skill:
    """Merge one or more detector codes that share a command into one file.

    Raises ValueError for a code with no known purpose -- silently skipping
    it would ship a skill with only half its rules explained.
    """
    purposes = [purpose_of(code) for code in codes]
    missing = [code for code, purpose in zip(codes, purposes) if purpose is None]
    if missing:
        raise ValueError(f"알 수 없는 신호: {', '.join(missing)}")

    command = purposes[0].command
    summary = purposes[0].summary if len(purposes) == 1 else " / ".join(
        dict.fromkeys(p.summary for p in purposes)
    )
    rules: list[str] = []
    for purpose in purposes:
        for rule in purpose.rules:
            if rule not in rules:
                rules.append(rule)

    return Skill(
        command=command,
        summary=summary,
        rules=tuple(rules),
        sources=tuple(codes),
        occurrences=sum(counts.get(code, 0) for code in codes),
    )


def group_by_command(codes: Iterable[str]) -> dict[str, list[str]]:
    """Which detector codes land on the same command, so they promote together."""
    groups: dict[str, list[str]] = {}
    for code in codes:
        purpose = purpose_of(code)
        if purpose is None:
            continue
        groups.setdefault(purpose.command, []).append(code)
    return groups
