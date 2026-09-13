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

Each ``Purpose`` also carries ``triggers`` -- short phrases naming the
situation, not the fix. A rule sentence ("탐색 범위를 좁힌다") tells a
person what the command does but not that their own prompt ("전체 다 고쳐줘")
is the situation it is for, and it gives Claude Code's own skill-routing
(which reads ``description`` to decide when to invoke a skill unasked)
far less to match against than the actual words involved. ``triggers``
fixes both: it goes into ``description`` for people scanning `/`
autocomplete, and into the routing signal Claude Code already uses.
"""

from __future__ import annotations

import json

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = ["Purpose", "Skill", "PURPOSE", "purpose_of", "render_skill", "skills_root",
          "group_by_command", "usage_counts", "live_commands",
          "TEXT_OPTIMIZER_SKILL", "text_optimizer_available"]

DESCRIPTION_LIMIT = 500
BODY_LINE_LIMIT = 60


@dataclass(frozen=True)
class Purpose:
    command: str  # the /name it becomes
    summary: str  # one line: what this actually does, for the report
    rules: tuple[str, ...]  # the behaviour, once promoted
    triggers: tuple[str, ...] = ()  # short phrases naming the situation, for description + routing


# One entry per detector in signals.CODES. Two codes may point at the same
# command (SCOPE_BLOWUP and VAGUE_SCOPE are both "the ask was too open");
# render_skill merges their rules instead of writing two files.
#
# ``triggers`` matters for two different readers:
#   - a person scanning `/` autocomplete, who cannot tell "탐색 범위를 좁힌다"
#     applies to THEIR situation without an example of what that situation
#     looks like;
#   - Claude Code's own routing, which (per its docs) uses `description` to
#     decide when to invoke a skill automatically -- a bare behaviour
#     sentence gives it far less to match against than the actual words a
#     prompt uses ("전체", "다 고쳐줘") or the situation to notice in its own
#     actions ("이미 build를 한 번 돌렸는데 또 돌리려는 참").
PURPOSE: dict[str, Purpose] = {
    "REDISCOVERY": Purpose(
        "focus-file",
        "지목한 파일만 읽고, 그 파일을 다시 열지 않는다.",
        (
            "사용자가 지목한 파일만 연다. 열기 전에 전체를 한 번에 읽어 내용을 기억한다.",
            "같은 파일을 이미 읽었으면 다시 열지 않는다 -- 기억한 내용을 그대로 쓴다.",
            "사용자가 새로 지목하기 전까지 다른 파일은 열지 않는다.",
        ),
        ("같은 파일을 또 열려는 순간",),
    ),
    "SCOPE_BLOWUP": Purpose(
        "scoped-edit",
        "탐색 범위를 사용자가 말한 곳으로 좁힌다.",
        (
            "사용자가 지목한 파일·화면만 수정 대상으로 삼는다.",
            "관련 여부가 불확실한 파일은 열기 전에 먼저 사용자에게 확인한다.",
            "프로젝트 전체 탐색은 사용자가 명시적으로 요청했을 때만 한다.",
        ),
        ("전체", "모든", "다 고쳐줘", "정리해줘", "점검해줘", "리팩터해줘"),
    ),
    "BUILD_LOOP": Purpose(
        "quick-test",
        "작은 수정은 모아서, 큰 기능은 바로바로 build·test한다.",
        (
            "작은 수정(오타·문구·사소한 로직) 여러 개는 대략 5개 정도 모아서 한 번에 "
            "build·test로 확인한다 -- 하나 고칠 때마다 돌리지 않는다.",
            "규모가 큰 기능(새 모듈, 핵심 로직 변경)은 만든 직후 바로 build·test로 "
            "확인한다 -- 다른 작업과 묶어서 나중에 한꺼번에 확인하지 않는다.",
            "위 두 경우가 아니면, 이미 실행한 build나 test를 사용자가 다시 요청하기 "
            "전까지 또 실행하지 않는다.",
            "실패하면 원인을 고치고 한 번 더 실행한다 -- 그 이상 반복하지 않는다.",
        ),
        ("이미 build나 test를 실행했는데 또 실행하려는 순간",),
    ),
    "THRASH": Purpose(
        "ui-edit",
        "원하는 결과를 먼저 못박고, 고친 뒤 되돌리지 않는다.",
        (
            "시작 전 사용자가 원하는 최종 결과를 한 줄로 요약해 확인한다.",
            "그 결과에 필요한 부분만 고치고, 관련 없는 다른 부분은 손대지 않는다.",
            "다 고친 뒤 한 번만 확인하고 끝낸다 -- 고쳤다 되돌리는 것을 반복하지 않는다.",
        ),
        ("같은 부분을 고쳤다 되돌리기를 반복하려는 순간",),
    ),
    "CONTEXT_REPEAT": Purpose(
        "project-brief",
        "프로젝트 설명을 매번 다시 안 쓰도록 AGENTS.md를 먼저 읽는다.",
        (
            "작업 시작 전 프로젝트 루트의 AGENTS.md(없으면 README.md)를 먼저 읽는다.",
            "거기 적힌 내용은 사용자가 다시 말하지 않아도 되므로 다시 묻지 않는다.",
            "AGENTS.md가 없는데 사용자가 프로젝트를 길게 설명했다면, 다음에 또 "
            "설명할 필요 없도록 AGENTS.md를 새로 만들지 물어본다.",
        ),
        ("사용자가 프로젝트 설명을 이전과 비슷하게 다시 적으려는 순간",),
    ),
    "VAGUE_SCOPE": Purpose(
        "scoped-edit",
        "대상이 없는 요청은 시작 전에 먼저 되묻는다.",
        (
            "요청에 대상(파일·화면·기능)이 없으면, 도구를 쓰기 전에 무엇을 고칠지 먼저 물어본다.",
            "'전체', '다', '알아서' 같은 말이 나오면 구체적인 범위로 되물은 뒤 시작한다.",
        ),
        ("전체", "다", "알아서", "적당히", "문제 있으면"),
    ),
    "UNDERSCOPED_FAIL": Purpose(
        "wider-edit",
        "이런 작업은 한 파일로 좁히지 않고 관련 파일까지 함께 본다.",
        (
            "이 종류의 작업은 관련 파일 1~2개를 함께 확인하는 것부터 시작한다.",
            "고친 뒤 관련 화면·기능이 함께 깨지지 않았는지 한 번 더 확인한다.",
        ),
        ("좁게 고쳤는데 같은 요청이 곧바로 다시 오는 순간",),
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
    triggers: tuple[str, ...] = ()

    @property
    def description(self) -> str:
        """The line Claude Code shows next to the name in `/` autocomplete,
        and what its own routing reads to decide whether to invoke this
        skill without being asked by name.

        Three things a person -- or the model -- needs, in one line: why
        this command exists (count), what it does (summary), and the
        situation that means it applies (triggers). The rule text alone
        ("지목한 파일만 읽는다") answers the middle question only; a beginner
        scanning the list still cannot tell it is THEIR situation without
        the third part.
        """
        base = f"AI_saver 자동 생성 ({self.occurrences}회 감지) -- {self.summary}"
        if not self.triggers:
            return base
        return f"{base} 이런 상황: {', '.join(self.triggers)}."

    def render(self) -> str:
        body = [
            "---",
            f"name: {self.command}",
            f"description: {_yaml_string(self.description)}",
            "---",
            "",
            f"AI_saver가 지난 기록에서 같은 습관을 {self.occurrences}번 감지해 만들었습니다.",
            "",
        ]
        if self.triggers:
            body.append(f"**언제 씀**: {', '.join(self.triggers)}")
            body.append("")
        body += [f"- {rule}" for rule in self.rules]
        text = "\n".join(body) + "\n"
        lines = text.count("\n")
        if lines > BODY_LINE_LIMIT:
            raise ValueError(f"/{self.command}: 본문 {lines}줄 > 예산 {BODY_LINE_LIMIT}줄")
        if len(self.description) > DESCRIPTION_LIMIT:
            raise ValueError(f"/{self.command}: description {len(self.description)}자 > 예산 {DESCRIPTION_LIMIT}자")
        return text

    def write(self, root: Path | None = None, platform: str = "codex") -> Path:
        folder = (root or skills_root(platform)) / self.command
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "SKILL.md"
        path.write_text(self.render(), encoding="utf-8")
        return path


def _yaml_string(text: str) -> str:
    """A YAML double-quoted scalar for ``text``.

    A plain (unquoted) YAML scalar cannot contain ": " -- a colon followed
    by a space is ambiguous with mapping syntax and breaks the parser.
    ``triggers`` phrases are joined into the description as "이런 상황: ..."
    on purpose, so unquoted was never safe here; this bit for real, silently
    (the file still loaded as *something*, just not the description meant).
    JSON string escaping is a valid subset of YAML's double-quoted scalar
    syntax, so ``json.dumps`` does the escaping without pulling in a YAML
    dependency this project otherwise has no use for.
    """
    return json.dumps(text, ensure_ascii=False)


def skills_root(platform: str = "codex") -> Path:
    """Personal skills folder watched by the selected coding agent."""
    if platform == "codex":
        return Path.home() / ".codex" / "skills"
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
    triggers: list[str] = []
    for purpose in purposes:
        for rule in purpose.rules:
            if rule not in rules:
                rules.append(rule)
        for trigger in purpose.triggers:
            if trigger not in triggers:
                triggers.append(trigger)

    return Skill(
        command=command,
        summary=summary,
        rules=tuple(rules),
        sources=tuple(codes),
        occurrences=sum(counts.get(code, 0) for code in codes),
        triggers=tuple(triggers),
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


def usage_counts(records: Sequence[Mapping]) -> Counter:
    """How many turns actually had each command active, straight from the
    transcript's own ``attributionSkill`` field.

    This is a different question from ``effect.evaluate``'s "did the habit
    shrink" -- a command nobody types is dead weight even if the underlying
    habit happened to improve some other way, and a command whose habit
    hasn't dropped yet might still be in daily use. Both signals matter;
    neither substitutes for the other.

    Only present on turns recorded after this field existed -- older ledger
    entries have no "skills" key and simply contribute nothing, the same
    way a brand-new signal always starts at zero rather than raising.
    """
    counts: Counter = Counter()
    for record in records:
        if record.get("kind") != "turn":
            continue
        counts.update(record.get("skills") or [])
    return counts


def live_commands(root: Path | None = None) -> set[str]:
    """Which promoted commands actually exist on disk right now."""
    base = root or skills_root()
    if not base.exists():
        return set()
    return {p.name for p in base.iterdir() if (p / "SKILL.md").exists()}


TEXT_OPTIMIZER_SKILL = "text-optimizer"


def text_optimizer_available(root: Path | None = None) -> bool:
    """Whether the external ``text-optimizer`` skill is installed.

    A Python subprocess cannot invoke a Claude skill -- that runs the
    model, and this CLI has none. So this is as far as ``promote`` itself
    goes: detect (free, mechanical) and report. Whoever is driving the
    promotion -- a person, or ``skill-promote`` run by Claude -- decides
    whether the small compression pass is worth the tokens and, if so,
    actually invokes the skill on the new file.
    """
    return TEXT_OPTIMIZER_SKILL in live_commands(root)
