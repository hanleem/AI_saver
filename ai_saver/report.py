"""Turning the ledger into something a beginner will act on.

Interface
---------
    render_month(month, records, technical=False) -> str

House rule: no raw token counts in the normal report. "42,378 cache_read
tokens" tells a beginner nothing and makes the tool feel like a bill.
"로그인 화면을 고칠 때마다 앱 전체를 다시 읽었어요" tells them what to do
differently. The numbers live in the technical appendix, behind a flag.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Mapping, Sequence

from .signals import CODES

__all__ = ["render_month", "SKILL_MIN"]

SKILL_MIN = 5  # same habit this many times before a /command is worth proposing

_ADVICE: Mapping[str, tuple[str, str]] = {
    "REDISCOVERY": (
        "같은 파일을 계속 다시 읽었습니다.",
        "고칠 파일을 처음부터 이름으로 찍어 주세요. 예: `login.tsx만 고쳐. 다른 파일은 열지 마.`",
    ),
    "SCOPE_BLOWUP": (
        "많이 뒤졌는데 실제로 바뀐 건 한두 개였습니다.",
        "범위를 먼저 닫아 주세요. 예: `이 화면만. 연결된 다른 페이지는 손대지 마.`",
    ),
    "BUILD_LOOP": (
        "같은 빌드·테스트를 여러 번 돌렸습니다.",
        "실행 횟수를 정해 주세요. 예: `수정 다 끝난 뒤 build는 마지막에 한 번만.`",
    ),
    "THRASH": (
        "고치고 되돌리기를 반복했습니다.",
        "원하는 결과를 먼저 한 줄로 못 박아 주세요. 예: `버튼 색만 파란색으로. 나머지는 그대로.`",
    ),
    "CONTEXT_REPEAT": (
        "매번 같은 설명을 다시 적었습니다.",
        "프로젝트 설명은 CLAUDE.md에 한 번만 적어두면 매번 안 써도 됩니다.",
    ),
    "VAGUE_SCOPE": (
        "무엇을 고칠지 정하지 않고 시작했습니다.",
        "첫 줄에 대상을 적어 주세요. 예: `설정 화면의 저장 버튼만.`",
    ),
    "UNDERSCOPED_FAIL": (
        "너무 좁게 잡아서 같은 요청을 다시 했습니다.",
        "이런 작업은 다음부터 한 단계 넓게(B) 잡는 편이 빨랐습니다.",
    ),
}


def render_month(month: str, records: Sequence[Mapping], technical: bool = False) -> str:
    turns = [r for r in records if r.get("kind") == "turn"]
    gates = [r for r in records if r.get("kind") == "gate"]
    if not turns:
        return f"# AI_saver — {month}\n\n아직 기록이 없습니다. `ai-saver backfill`을 먼저 돌려 주세요.\n"

    findings = [(s, r) for r in turns for s in r.get("signals") or []]
    counts = Counter(s["code"] for s, _ in findings)
    total_cost = sum(float(r.get("cost") or 0) for r in turns) or 1.0
    wasted = sum(float(s.get("wasted") or 0) for s, _ in findings)
    touched = len({r.get("id") for _, r in findings})

    out = [f"# AI_saver — {month}", ""]
    out.append(f"**작업 {len(turns)}번 중 {touched}번**에서 되돌아간 흔적이 보였습니다. "
               f"대략 **{_share(wasted, total_cost)}** 정도의 작업이 반복이었어요.")
    out.append("")
    out.append("> 되돌아간 양은 추정치입니다. 쓴 양은 정확히 기록되지만, "
               "그중 무엇이 꼭 필요했는지는 기계가 확실히 알 수 없습니다.")
    out.append("")

    out.append("## 이번 달 습관")
    out.append("")
    if not counts:
        out.append("눈에 띄는 낭비 패턴이 없었습니다.")
    for code, count in counts.most_common(3):
        headline, advice = _ADVICE.get(code, (CODES.get(code, code), ""))
        example = next((s["detail"] for s, _ in findings if s["code"] == code and s.get("detail")), "")
        out.append(f"### {headline} ({count}번)")
        if example:
            out.append(f"- 예: {example}")
        out.append(f"- 다음엔 이렇게: {advice}")
        out.append("")

    candidates = [(code, count) for code, count in counts.most_common() if count >= SKILL_MIN]
    out.append("## /명령어 후보")
    out.append("")
    if candidates:
        for code, count in candidates:
            out.append(f"- `{_command_name(code)}` — 같은 상황이 {count}번. "
                       f"규칙을 한 번 정해두면 매번 안 적어도 됩니다.")
        out.append("")
        out.append("승격하려면 `skill-promote` skill을 부르세요. "
                   "상시 비용이 절감보다 크면 만들지 않습니다.")
    else:
        out.append(f"아직 없습니다. 같은 습관이 {SKILL_MIN}번 이상 반복돼야 후보가 됩니다.")
    out.append("")

    if gates:
        high = sum(1 for g in gates if g.get("level") in ("HIGH", "VERY_HIGH"))
        picked = Counter(r.get("choice") for r in records
                         if r.get("kind") in ("gate", "choice") and r.get("choice"))
        out.append("## 실행 전 확인")
        out.append("")
        out.append(f"- 프롬프트 {len(gates)}개 중 {high}개가 '작업량 큼'으로 판정됐습니다 "
                   f"({_share(high, len(gates))}).")
        if picked:
            out.append("- 고른 항목: " + ", ".join(f"{k} {v}번" for k, v in sorted(picked.items())))
        out.append("")

    if technical:
        out.extend(_appendix(turns, gates, total_cost, wasted))

    return "\n".join(out) + "\n"


def _appendix(turns, gates, total_cost: float, wasted: float) -> list[str]:
    by_project: dict[str, float] = defaultdict(float)
    for record in turns:
        # Folder name only. A report is the one file a person is likely to
        # show someone else, and a full path carries their user name.
        cwd = str(record.get("cwd") or "?").replace("\\", "/").rstrip("/")
        by_project[cwd.rsplit("/", 1)[-1] or "?"] += float(record.get("cost") or 0)
    cache_read = sum(int(r.get("cache_read") or 0) for r in turns)
    fresh = sum(int(r.get("input") or 0) + int(r.get("cache_creation") or 0) for r in turns)

    out = ["## 부록 (숫자)", "",
           f"- 가중 토큰 합계: {total_cost:,.0f}",
           f"- 되돌아간 추정분: {wasted:,.0f} ({_share(wasted, total_cost)})",
           f"- 캐시 읽기 비중: {_share(cache_read, cache_read + fresh)}",
           f"- 작업당 평균 도구 호출: {sum(int(r.get('calls') or 0) for r in turns) / max(len(turns), 1):.1f}회",
           ""]
    if gates:
        scores = [int(g.get("score") or 0) for g in gates]
        out.append(f"- 게이트 점수 중앙값: {sorted(scores)[len(scores) // 2]}")
        out.append("")
    out.append("| 프로젝트 | 가중 토큰 |")
    out.append("|---|---|")
    for project, cost in sorted(by_project.items(), key=lambda kv: -kv[1])[:8]:
        out.append(f"| {project} | {cost:,.0f} |")
    return out


def _share(part: float, whole: float) -> str:
    if whole <= 0:
        return "0%"
    return f"{round(100 * part / whole)}%"


def _command_name(code: str) -> str:
    return {
        "REDISCOVERY": "/focus-file",
        "SCOPE_BLOWUP": "/small-edit",
        "BUILD_LOOP": "/quick-test",
        "THRASH": "/ui-edit",
        "CONTEXT_REPEAT": "/project-brief",
        "VAGUE_SCOPE": "/scoped-edit",
        "UNDERSCOPED_FAIL": "/wider-edit",
    }.get(code, "/" + code.lower().replace("_", "-"))
