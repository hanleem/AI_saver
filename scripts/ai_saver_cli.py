#!/usr/bin/env python3
"""AI_saver command line.

    python ai_saver_cli.py backfill [--days 60]   past transcripts -> ledger
    python ai_saver_cli.py report [2026-09] [-t]  ledger -> readable report
    python ai_saver_cli.py calibrate              keep interruptions rare
    python ai_saver_cli.py gate on|off            leave or exit shadow mode
    python ai_saver_cli.py status                 what AI_saver knows so far

backfill is the one to run first: the transcripts are already on disk, so a
baseline exists before the tool changes anything. Without that baseline
there is no way to prove the tool helped.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_saver.ledger import Ledger, turn_record  # noqa: E402
from ai_saver.profile import Profile, data_root  # noqa: E402
from ai_saver.report import render_month  # noqa: E402
from ai_saver.signals import detect  # noqa: E402
from ai_saver.transcript import read_all_turns, transcript_root  # noqa: E402


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
    text = render_month(month, ledger.month(month), technical=args.technical)
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
