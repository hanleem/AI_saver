"""Finding waste in work that already happened.

Interface
---------
    detect(turns, decisions=None) -> list[Finding]

Adding, removing or retuning a detector must not change this line. Every
detector is mechanical -- no model call -- because the measurement layer of
a token-saving tool must not itself cost tokens.

``wasted`` is an estimate, and the report says so. The cost of a turn is
known exactly; how much of it was avoidable is apportioned from the shape
of the turn.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .gate import assess
from .profile import Profile
from .transcript import BUILD, EDIT, READ, Turn

__all__ = ["Finding", "detect", "CODES"]

CODES = {
    "REDISCOVERY": "같은 파일을 여러 번 다시 읽음",
    "SCOPE_BLOWUP": "많이 뒤졌는데 실제로 고친 건 한두 개",
    "BUILD_LOOP": "같은 빌드·테스트를 여러 번 반복",
    "THRASH": "고치고 돌리고를 계속 되풀이",
    "CONTEXT_REPEAT": "같은 설명을 매번 다시 씀",
    "VAGUE_SCOPE": "범위를 안 정해서 넓게 훑음",
    "UNDERSCOPED_FAIL": "너무 좁게 잡아서 다시 요청함",
}

_REDISCOVERY_MIN = 3
_BLOWUP_CALLS = 25
_BLOWUP_EDITS = 2
_BUILD_MIN = 3
_THRASH_CYCLES = 4
_REPEAT_OVERLAP = 0.40


@dataclass(frozen=True)
class Finding:
    code: str
    prompt_id: str
    session_id: str
    detail: str
    wasted: float

    @property
    def label(self) -> str:
        return CODES.get(self.code, self.code)


def detect(turns: Sequence[Turn], decisions: Mapping[str, str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    profile = Profile()

    for turn in turns:
        findings.extend(_rediscovery(turn))
        findings.extend(_scope_blowup(turn))
        findings.extend(_build_loop(turn))
        findings.extend(_thrash(turn))
        findings.extend(_vague_scope(turn, profile))

    for session in _by_session(turns):
        findings.extend(_context_repeat(session))
        findings.extend(_underscoped_fail(session, decisions or {}))

    findings.sort(key=lambda f: f.wasted, reverse=True)
    return findings


# --- detectors -----------------------------------------------------------


def _rediscovery(turn: Turn) -> Iterable[Finding]:
    for target, count in Counter(turn.targets(READ)).items():
        if count >= _REDISCOVERY_MIN:
            yield _finding(turn, "REDISCOVERY", f"{_short(target)} — {count}번 읽음", count - 1)


def _scope_blowup(turn: Turn) -> Iterable[Finding]:
    edits = len(set(turn.targets(EDIT)))
    if len(turn.calls) > _BLOWUP_CALLS and edits <= _BLOWUP_EDITS:
        share = 0.5 * turn.cost
        yield Finding("SCOPE_BLOWUP", turn.prompt_id, turn.session_id,
                      f"{len(turn.calls)}번 뒤져서 {edits}개 수정", share)


def _build_loop(turn: Turn) -> Iterable[Finding]:
    for target, count in Counter(turn.targets(BUILD)).items():
        if count >= _BUILD_MIN:
            yield _finding(turn, "BUILD_LOOP", f"{_short(target)} — {count}번 실행", count - 1)


def _thrash(turn: Turn) -> Iterable[Finding]:
    edits = Counter(turn.targets(EDIT))
    builds = len(turn.targets(BUILD))
    for target, count in edits.items():
        if count >= _THRASH_CYCLES and builds >= 2:
            yield _finding(turn, "THRASH", f"{_short(target)} — {count}번 고침", count - 2)


def _vague_scope(turn: Turn, profile: Profile) -> Iterable[Finding]:
    if not turn.prompt:
        return
    verdict = assess(turn.prompt, profile)
    if verdict.intervene and len(turn.calls) > 10:
        yield Finding("VAGUE_SCOPE", turn.prompt_id, turn.session_id,
                      ", ".join(verdict.reasons), 0.3 * turn.cost)


def _context_repeat(session: Sequence[Turn]) -> Iterable[Finding]:
    seen: list[tuple[Turn, set[str]]] = []
    for turn in session:
        grams = _trigrams(turn.prompt)
        if len(grams) >= 8:
            for earlier, earlier_grams in seen:
                if _overlap(grams, earlier_grams) > _REPEAT_OVERLAP:
                    yield Finding("CONTEXT_REPEAT", turn.prompt_id, turn.session_id,
                                  "앞 요청과 설명이 거의 같음", 0.1 * turn.cost)
                    break
        seen.append((turn, grams))


def _underscoped_fail(session: Sequence[Turn], decisions: Mapping[str, str]) -> Iterable[Finding]:
    """The self-correction detector: proof that a narrow recommendation was wrong.

    Without it the coach drifts towards always recommending the smallest
    option, which is cheap per turn and expensive per finished task.
    """
    for index, turn in enumerate(session):
        if decisions.get(turn.prompt_id) != "A":
            continue
        grams = _trigrams(turn.prompt)
        for later in session[index + 1: index + 3]:
            if grams and _overlap(grams, _trigrams(later.prompt)) > _REPEAT_OVERLAP:
                yield Finding("UNDERSCOPED_FAIL", turn.prompt_id, turn.session_id,
                              "좁게 잡았다가 같은 요청이 다시 나옴", later.cost)
                break


# --- implementation ------------------------------------------------------


def _finding(turn: Turn, code: str, detail: str, extra_calls: int) -> Finding:
    """Apportion turn cost to the redundant calls inside it."""
    share = turn.cost * (extra_calls / max(len(turn.calls), 1))
    return Finding(code, turn.prompt_id, turn.session_id, detail, share)


def _by_session(turns: Sequence[Turn]) -> list[list[Turn]]:
    grouped: dict[str, list[Turn]] = defaultdict(list)
    for turn in turns:
        grouped[turn.session_id].append(turn)
    return list(grouped.values())


_WORD = re.compile(r"[0-9a-z가-힣]+")


def _trigrams(text: str) -> set[str]:
    words = _WORD.findall(text.lower())
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _short(target: str) -> str:
    """Shorten a file path to its name; leave a command alone."""
    if " " in target:
        return target[:48]
    return target.rsplit("/", 1)[-1][:48] or target[:48]
