#!/usr/bin/env python3
"""AI_saver command line.

    python ai_saver_cli.py backfill [--days 60]   past transcripts -> ledger
    python ai_saver_cli.py report [2026-09] [-t]  ledger -> readable report
    python ai_saver_cli.py calibrate              keep interruptions rare
    python ai_saver_cli.py gate on|off            leave or exit shadow mode
    python ai_saver_cli.py status                 what AI_saver knows so far
    python ai_saver_cli.py promote --list         which habits qualify
    python ai_saver_cli.py promote focus-file     write the skill -- makes /focus-file real
    python ai_saver_cli.py wiki                   show the editable 4-option wording
    python ai_saver_cli.py wiki stats             recommendation-vs-choice numbers, per task
    python ai_saver_cli.py wiki reset             put the wording back to defaults

backfill is the one to run first: the transcripts are already on disk, so a
baseline exists before the tool changes anything. Without that baseline
there is no way to prove the tool helped.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_saver import effect, optionwiki  # noqa: E402
from ai_saver.ledger import Ledger, promotion_record, turn_record  # noqa: E402
from ai_saver.profile import Profile, data_root  # noqa: E402
from ai_saver.promotion import (  # noqa: E402
    PURPOSE, group_by_command, render_skill, skills_root, text_optimizer_available,
)
from ai_saver.report import SKILL_MIN, habit_counts, render_month  # noqa: E402
from ai_saver.signals import detect  # noqa: E402
from ai_saver.transcript import read_all_turns, transcript_root  # noqa: E402

PROMOTION_WINDOW_DAYS = 30


def backfill(args) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    turns = read_all_turns(Path(args.root) if args.root else transcript_root(), since=since)
    if not turns:
        print("트랜스크립트를 찾지 못했습니다.")
        return 1

    decisions = {t.prompt_id: t.choices[0] for t in turns if t.choices and t.prompt_id}
    findings = detect(turns, decisions)
    written = Ledger(profile=Profile.load()).append(
        turn_record(turn, findings) for turn in turns if turn.prompt_id
    )

    months = sorted({t.month for t in turns})
    print(f"작업 {len(turns)}건, 낭비 신호 {len(findings)}건 → 원장 {written}건 기록 ({', '.join(months)})")
    print(f"다음: python {Path(__file__).name} report {months[-1]}")
    return 0


def report(args) -> int:
    ledger = Ledger(profile=Profile.load())
    month = args.month or (ledger.months()[-1] if ledger.months() else
                           datetime.now().strftime("%Y-%m"))
    all_records = ledger.all_records()
    promoted = frozenset(r["command"] for r in all_records
                         if r.get("kind") == "promotion" and r.get("command"))

    text = render_month(month, ledger.month(month), technical=args.technical, promoted=promoted)

    effects_text = effect.render(effect.evaluate(all_records))
    if effects_text:
        text += "\n" + effects_text

    path = data_root() / "reports" / f"{month}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n저장: {path}")
    return 0


def calibrate(args) -> int:
    profile = Profile.load()
    ledger = Ledger(profile=profile)
    scores = [int(r.get("score") or 0) for month in ledger.months()
              for r in ledger.month(month) if r.get("kind") == "gate"]
    if len(scores) < 20:
        print(f"아직 표본이 적습니다 ({len(scores)}건). 20건 이상 쌓이면 조정합니다.")
        return 0
    tuned = profile.calibrated(scores)
    tuned.save()
    rate = sum(1 for s in scores if s >= tuned.threshold) / len(scores)
    print(f"기준점 {profile.threshold} → {tuned.threshold} "
          f"(개입 예상 {rate:.0%}, 목표 {tuned.target_high_rate:.0%})")
    return 0


def gate(args) -> int:
    from dataclasses import replace
    profile = Profile.load()
    replace(profile, gate_enabled=(args.state == "on")).save()
    print("실행 전 확인: " + ("켬" if args.state == "on" else "끔(관찰만)"))
    return 0


def _recent_records(ledger: Ledger, days: int = PROMOTION_WINDOW_DAYS) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [r for r in ledger.all_records() if str(r.get("ts") or "") >= cutoff]


def promote(args) -> int:
    ledger = Ledger(profile=Profile.load())
    recent = _recent_records(ledger)
    counts = habit_counts(recent)

    if args.list:
        eligible = {code: n for code, n in counts.items() if n >= SKILL_MIN}
        if not eligible:
            print(f"최근 {PROMOTION_WINDOW_DAYS}일간 {SKILL_MIN}번 이상 반복된 습관이 없습니다.")
            return 0
        for command, codes in group_by_command(list(eligible)).items():
            total = sum(eligible.get(c, 0) for c in codes)
            print(f"/{command} — 최근 {PROMOTION_WINDOW_DAYS}일간 {total}번  "
                  f"(python {Path(__file__).name} promote {command})")
        return 0

    if not args.command:
        print("어떤 걸 승격할지 지정하세요. 후보를 보려면: promote --list")
        return 1

    wanted = args.command.lstrip("/")
    codes = [code for code, purpose in PURPOSE.items() if purpose.command == wanted]
    if not codes:
        known = sorted({p.command for p in PURPOSE.values()})
        print(f"'{wanted}'은 모르는 이름입니다. 가능한 것: {', '.join(known)}")
        return 1

    total = sum(counts.get(code, 0) for code in codes)
    if total < SKILL_MIN and not args.force:
        print(f"최근 {PROMOTION_WINDOW_DAYS}일간 {total}번뿐입니다 (기준 {SKILL_MIN}번). "
              f"더 반복된 뒤에 다시 시도하세요. 그래도 지금 만들려면 --force를 붙이세요.")
        return 1

    skill = render_skill(codes, counts)
    path = skill.write(Path(args.root) if args.root else None)
    ledger.append([promotion_record(skill.command, codes, total, PROMOTION_WINDOW_DAYS)])

    print(f"만들었습니다: {path}")
    print(f"지금 Claude Code에서 `/{skill.command}` 을 쳐보세요 — 자동완성에 바로 뜹니다.")
    print(f"하는 일: {skill.summary}")
    print(f"{effect.MIN_OBSERVATION_DAYS}일 뒤부터 리포트에 효과가 있었는지 자동으로 나옵니다.")

    # promote itself never calls a model -- it can only detect that
    # text-optimizer exists (a file-system check, still free) and name it as
    # an option. Whether it's worth the tokens on a file this small is a
    # judgment call for whoever is driving this, not something the CLI decides.
    if text_optimizer_available():
        print(f"참고: text-optimizer skill이 설치돼 있습니다. "
              f"이 문구를 더 줄이고 싶으면 그 skill로 {path}를 검토해보세요.")
    return 0


def wiki(args) -> int:
    if args.action == "reset":
        path = optionwiki.reset(Path(args.root) if args.root else None)
        print(f"기본값으로 되돌렸습니다: {path}")
        return 0

    if args.action == "stats":
        ledger = Ledger(profile=Profile.load())
        gates = [r for r in ledger.all_records() if r.get("kind") == "gate" and r.get("task")]
        if not gates:
            print("아직 판정 기록이 없습니다.")
            return 0
        by_task: dict[str, list[dict]] = {}
        for record in gates:
            by_task.setdefault(record["task"], []).append(record)
        for task in sorted(by_task):
            records = by_task[task]
            recommended = Counter(r.get("recommended") for r in records if r.get("recommended"))
            chosen = Counter(r.get("choice") for r in records if r.get("choice"))
            answered = sum(chosen.values())
            print(f"[{task}] 판정 {len(records)}건, 답변 {answered}건")
            print(f"  추천 분포   {dict(sorted(recommended.items()))}")
            if answered:
                mismatch = sum(1 for r in records if r.get("choice") and r.get("choice") != r.get("recommended"))
                print(f"  실제 선택   {dict(sorted(chosen.items()))}  "
                      f"(추천과 다르게 고른 비율 {mismatch / answered:.0%})")
        print(f"\n위키 파일: {optionwiki.wiki_path()}")
        print("추천과 실제 선택이 자주 어긋나는 유형이 있으면, 그 유형의 문구나 별점을 위 파일에서 고치세요.")
        return 0

    path = optionwiki.ensure(Path(args.root) if args.root else None)
    print(path.read_text(encoding="utf-8"))
    return 0


def status(args) -> int:
    profile = Profile.load()
    ledger = Ledger(profile=profile)
    months = ledger.months()
    print(f"데이터 위치   {data_root()}")
    print(f"모드         {'개입' if profile.gate_enabled else '관찰만(shadow)'}")
    print(f"기준점       {profile.threshold} (목표 개입률 {profile.target_high_rate:.0%})")
    print(f"프롬프트 저장 {'함' if profile.store_prompts else '안 함(해시만)'}")
    if not months:
        print("원장         비어 있음 — backfill을 먼저 실행하세요")
        return 0
    for month in months:
        records = ledger.month(month)
        turns = sum(1 for r in records if r.get("kind") == "turn")
        gates = sum(1 for r in records if r.get("kind") == "gate")
        print(f"  {month}    작업 {turns}건 / 판정 {gates}건")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-saver", description="AI_saver")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("backfill", help="지난 기록으로 기준선 만들기")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--root", default="")
    p.set_defaults(func=backfill)

    p = subparsers.add_parser("report", help="월간 리포트")
    p.add_argument("month", nargs="?", default="")
    p.add_argument("-t", "--technical", action="store_true")
    p.set_defaults(func=report)

    p = subparsers.add_parser("calibrate", help="개입 빈도 조정")
    p.set_defaults(func=calibrate)

    p = subparsers.add_parser("gate", help="실행 전 확인 켜기/끄기")
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=gate)

    p = subparsers.add_parser("status", help="현재 상태")
    p.set_defaults(func=status)

    p = subparsers.add_parser("promote", help="반복 습관을 실제 /명령어로 만들기")
    p.add_argument("command", nargs="?", default="", help="예: focus-file")
    p.add_argument("--list", action="store_true", help="후보만 보기")
    p.add_argument("--force", action="store_true", help="기준 미달이어도 만들기")
    p.add_argument("--root", default="", help="테스트용: 다른 폴더에 쓰기")
    p.set_defaults(func=promote)

    p = subparsers.add_parser("wiki", help="4지선다 문구 위키 -- 보기/통계/초기화")
    p.add_argument("action", nargs="?", default="show", choices=["show", "stats", "reset"])
    p.add_argument("--root", default="", help="테스트용: 다른 폴더 사용")
    p.set_defaults(func=wiki)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
