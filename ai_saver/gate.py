"""Judging a prompt before it runs.

Interface
---------
    assess(prompt, profile, options=None) -> Verdict

One call, no I/O, no model call, no network. The whole point of this module
is that deciding *whether* a request is expensive must itself be free --
if a model had to judge every prompt, the judging would cost more than the
waste it prevents.

The four-option wording is a parameter, not a constant: `options` maps task
name to its four `Option`s and defaults to `DEFAULT_OPTIONS` (this module's
own hardcoded table) when omitted, so nothing changes for a caller that
never heard of the wiki. A caller that wants the *editable* wording --
every real hook does -- loads `optionwiki.load()` first and passes the
result in here. That load is where the disk read lives; this function
still never touches disk itself.

The rendering of the four options is left to the caller (the agent shows
them with its own question UI); this module only decides what to offer and
which one to recommend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .profile import Profile

__all__ = ["Option", "Verdict", "assess", "DEFAULT_OPTIONS", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"]

LOW, MEDIUM, HIGH, VERY_HIGH = "LOW", "MEDIUM", "HIGH", "VERY_HIGH"


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    stars: str
    rule: str  # the scope rule that applies if the user picks this


@dataclass(frozen=True)
class Verdict:
    level: str
    score: int
    task: str
    reasons: tuple[str, ...]
    options: tuple[Option, ...]
    recommended: str
    why: str

    @property
    def intervene(self) -> bool:
        return self.level in (HIGH, VERY_HIGH)

    def as_context(self) -> str:
        """The text injected into the turn. Kept short on purpose -- this is
        paid on every high-risk prompt."""
        lines = [
            f"[AI_saver] 이 요청은 작업량이 클 수 있습니다 ({', '.join(self.reasons)}).",
            "도구를 하나도 쓰기 전에 아래 4개를 그대로 사용자에게 보여주고 답을 기다리세요"
            "(질문 도구가 있으면 그것으로, 없으면 목록을 그대로 출력해서 물어보세요).",
        ]
        for option in self.options:
            mark = "  ← 추천" if option.key == self.recommended else ""
            lines.append(f"{option.key}. {option.label} {option.stars}{mark}")
        lines.append(f"추천 사유(한 줄로 보여줄 것): {self.why}")
        lines.append("사용자가 고른 항목의 범위 규칙을 지키고, 그 범위 밖은 건드리지 마세요.")
        return "\n".join(lines)


def assess(prompt: str, profile: Profile,
          options: Mapping[str, tuple[Option, ...]] | None = None) -> Verdict:
    text = prompt.strip()
    score, reasons = _score(text)
    task = _classify(text)
    table = options or DEFAULT_OPTIONS
    task_options = table.get(task) or DEFAULT_OPTIONS[task]
    level = _level(score, profile)
    recommended, why = _recommend(task, text, level)
    return Verdict(
        level=level,
        score=score,
        task=task,
        reasons=tuple(reasons) or ("범위가 넓음",),
        options=task_options,
        recommended=recommended,
        why=why,
    )


# --- implementation ------------------------------------------------------

# (pattern, points, reason shown to the user). Positive widens scope,
# negative narrows it. Korean and English both, because real prompts mix them.
_RULES: Sequence[tuple[re.Pattern[str], int, str]] = (
    (re.compile(r"전체|모든|전부|다\s*(고쳐|바꿔|확인|수정)|싹\s*다|통째로"
                r"|\ball\b|\bevery\b|\bentire\b|\bwhole\b|\bacross the\b", re.I), 30, "전체 범위"),
    (re.compile(r"문제\s*(있으면|있는지)|알아서|적당히|좀\s*(고쳐|바꿔)|정리\s*해|점검"
                r"|최적화\s*해|개선\s*해|리팩터|\brefactor\b|\baudit\b|\bclean\s*up\b|\bfix\s+(any|all)\b", re.I),
     20, "범위가 열려 있음"),
    (re.compile(r"여러|각각|하나씩|페이지들|화면들|\bpages\b|\bscreens\b|\bcomponents\b", re.I), 10, "대상이 여러 개"),
    (re.compile(r"프로젝트|레포|리포지토리|코드베이스|\brepo\b|\bcodebase\b|\bproject\b", re.I), 15, "프로젝트 단위"),
    # narrowing
    (re.compile(r"[\w./\\-]+\.(py|js|ts|tsx|jsx|html|css|md|json|yml|yaml|java|go|rs|c|cpp|cs|rb|php|sql|vue|svelte)\b", re.I),
     -25, ""),
    (re.compile(r"이\s*(파일|함수|줄|버튼|화면)|현재\s*(화면|파일|페이지)|여기\s*만|만\s*(고쳐|바꿔|수정)"
                r"|\bonly\b|\bjust this\b|\bthis file\b", re.I), -25, ""),
    (re.compile(r"계획만|분석만|설명만|어떻게|왜|알려줘|추천해|물어보|\bplan only\b|\bexplain\b|\bwhy\b|\bhow do\b", re.I),
     -20, ""),
)

_IMPERATIVE = re.compile(r"(해줘|하고|만들어|고쳐|바꿔|추가|삭제|수정)")


def _score(text: str) -> tuple[int, list[str]]:
    score, reasons = 0, []
    for pattern, points, reason in _RULES:
        if pattern.search(text):
            score += points
            if reason:
                reasons.append(reason)
    if not _has_anchor(text):
        score += 20
        reasons.append("대상 파일·화면 미지정")
    if len(_IMPERATIVE.findall(text)) >= 3:
        score += 10
        reasons.append("한 번에 여러 작업")
    if len(text) < 40 and score < 30:
        score -= 10
    return max(score, 0), reasons


_ANCHOR = re.compile(
    r"[\w./\\-]+\.(py|js|ts|tsx|jsx|html|css|md|json|yml|yaml|java|go|rs|c|cpp|cs|rb|php|sql|vue|svelte)\b"
    r"|[\"'`][^\"'`]{2,40}[\"'`]"
    r"|\b[a-z][a-zA-Z0-9]*(Component|Page|Screen|View|Service|Controller|Model)\b",
    re.I,
)


def _has_anchor(text: str) -> bool:
    return bool(_ANCHOR.search(text))


def _level(score: int, profile: Profile) -> str:
    if score >= profile.threshold + 30:
        return VERY_HIGH
    if score >= profile.threshold:
        return HIGH
    if score >= profile.threshold - 20:
        return MEDIUM
    return LOW


_TASKS: Sequence[tuple[str, re.Pattern[str]]] = (
    ("bug", re.compile(r"오류|에러|안\s*(되|돼)|버그|실패|깨졌|죽|\berror\b|\bbug\b|\bfail|\bcrash|\bbroken\b", re.I)),
    ("design", re.compile(r"디자인|예쁘|색|폰트|레이아웃|여백|UI|스타일|버튼|화면\s*(바꿔|수정)"
                          r"|\bdesign\b|\bstyle\b|\blayout\b|\bcolou?r\b|\bfont\b", re.I)),
    ("research", re.compile(r"찾아|조사|검색|알아봐|비교해|리서치|\bresearch\b|\bsearch\b|\bcompare\b|\bfind out\b", re.I)),
    ("data", re.compile(r"데이터|집계|통계|쿼리|csv|엑셀|표로|\bdata\b|\bquery\b|\bsql\b|\bexcel\b", re.I)),
)


def _classify(text: str) -> str:
    for task, pattern in _TASKS:
        if pattern.search(text):
            return task
    return "code"


def _opts(*rows: tuple[str, str, str, str]) -> tuple[Option, ...]:
    return tuple(Option(*row) for row in rows)


#: The built-in wording, used whenever no wiki-loaded table is supplied.
#: ``optionwiki.py`` treats this dict as the seed it writes out as the
#: first wiki file, so there is exactly one place these words are typed --
#: not two copies that can silently drift apart.
DEFAULT_OPTIONS: dict[str, tuple[Option, ...]] = {
    "code": _opts(
        ("A", "지정한 파일만 고치기", "★", "사용자가 지목한 파일만 읽고 수정한다. 다른 파일은 열지 않는다."),
        ("B", "관련 기능까지 고치기", "★★", "해당 기능이 걸친 파일까지만 읽고 수정한다. 전체 탐색은 하지 않는다."),
        ("C", "프로젝트 전체 점검", "★★★★", "프로젝트 전체를 탐색해 일괄 수정한다."),
        ("D", "고치지 말고 분석만", "★", "코드를 수정하지 않고 대상과 예상 작업량만 보고한다."),
    ),
    "design": _opts(
        ("A", "이 요소만 바꾸기", "★", "지목한 component와 그 style 파일만 수정한다. 기능 로직은 건드리지 않는다."),
        ("B", "이 화면 전체 맞추기", "★★", "현재 page와 거기 쓰인 component까지만 수정한다. design system은 그대로 둔다."),
        ("C", "앱 전체 통일", "★★★★", "관련 화면을 모두 탐색해 design system 수준에서 일괄 수정한다."),
        ("D", "먼저 시안만 보기", "★", "코드를 바꾸지 않고 변경안만 제시한다."),
    ),
    "bug": _opts(
        ("A", "오류 난 파일만 보기", "★", "스택트레이스에 나온 파일만 읽고 고친다."),
        ("B", "연결된 곳까지 따라가기", "★★", "해당 파일이 부르는 모듈까지만 추적한다."),
        ("C", "전체 진단", "★★★★", "프로젝트 전체 진단·테스트를 돌려 원인을 찾는다."),
        ("D", "원인만 설명", "★", "수정하지 않고 원인과 수정 방향만 보고한다."),
    ),
    "research": _opts(
        ("A", "빠르게 한 번 검색", "★", "검색 1~2회로 답한다."),
        ("B", "핵심 출처 확인", "★★", "핵심 출처 3~5개를 열어 교차 확인한다."),
        ("C", "전면 조사", "★★★★", "폭넓게 조사해 정리한다."),
        ("D", "검색 없이 지금 자료로", "★", "새로 검색하지 않고 이미 있는 자료로만 답한다."),
    ),
    "data": _opts(
        ("A", "파일 1개만 보기", "★", "지목한 파일·표만 읽는다."),
        ("B", "관련 표까지 보기", "★★", "직접 연결된 표까지만 읽는다."),
        ("C", "파이프라인 전체", "★★★★", "전체 파이프라인을 훑어 검증한다."),
        ("D", "구조만 확인", "★", "데이터를 처리하지 않고 스키마·규모만 보고한다."),
    ),
}


def _recommend(task: str, text: str, level: str) -> tuple[str, str]:
    """Pick one option and say why in one line. Never say 'C is expensive'."""
    if re.search(r"계획|분석만|설명만|어떻게|왜|\bplan\b|\bexplain\b", text, re.I):
        return "D", "지금은 고치는 것보다 먼저 무엇을 바꿀지 보는 게 빠릅니다."
    if level == VERY_HIGH and not _has_anchor(text):
        return "D", "대상이 정해지지 않아, 먼저 무엇을 바꿀지 정하는 편이 확실합니다."
    if task == "bug":
        return "A", "오류는 대개 난 자리에서 끝납니다. 안 되면 B로 넓히면 됩니다."
    if task == "research":
        return "B", "핵심 출처 몇 개면 충분해 보입니다."
    return "B", "이번 요청에는 프로젝트 전체를 볼 필요까지는 없어 보입니다."
