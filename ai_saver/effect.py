"""Did a promoted command actually change the habit it was made for?

Interface
---------
    evaluate(records, now=None) -> list[Effect]
    render(effects)             -> str

A skill earns its keep only if the habit that justified it shrank
afterward. This is the only place that compares "how often per day before
promotion" to "how often per day since" -- everything else (the CLI, the
report) just prints whatever verdict comes back.

Judging too early is worse than not judging: a skill needs a couple of
weeks of real use before a drop (or its absence) means anything, so a
fresh promotion reads as "too soon" rather than a false pass or fail.

Two independent questions get asked, not one:

  - did the underlying HABIT shrink (the rate comparison this module has
    always done)?
  - was the COMMAND itself actually used (straight from the transcript's
    ``attributionSkill`` field, via ``promotion.usage_counts``)?

A command can pass the first and fail the second (the habit improved for
some unrelated reason while nobody ever typed the command), and a command
barely used yet may still be too young to have moved the habit rate. Usage
is checked first: "essentially never used" is a simpler, more direct
verdict than a rate computation, and one that doesn't need the rate
question answered at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

__all__ = ["Effect", "evaluate", "render", "MIN_OBSERVATION_DAYS", "UNUSED_MIN_DAYS", "UNUSED_MAX_USES"]

MIN_OBSERVATION_DAYS = 14
# The habit's daily rate must fall to at most this fraction of its baseline
# rate to call the skill working. Anything short of that is noise, not proof.
WORKING_THRESHOLD = 0.7
# A command used this many times or fewer, a full review cycle after it was
# made, has not earned its standing cost regardless of what the habit rate says.
UNUSED_MIN_DAYS = 30
UNUSED_MAX_USES = 1


@dataclass(frozen=True)
class Effect:
    command: str
    codes: tuple[str, ...]
    days_since: int
    baseline_per_day: float
    current_per_day: float
    used: int
    verdict: str  # "TOO_SOON" | "UNUSED" | "WORKING" | "NOT_WORKING"

    @property
    def drop(self) -> int:
        if self.baseline_per_day <= 0:
            return 0
        return round(100 * (1 - self.current_per_day / self.baseline_per_day))

    @property
    def recommendation(self) -> str:
        if self.verdict == "TOO_SOON":
            remain = MIN_OBSERVATION_DAYS - self.days_since
            return f"판단하기엔 이릅니다. {remain}일 더 지켜보세요."
        if self.verdict == "UNUSED":
            return (f"{self.days_since}일 동안 {self.used}번밖에 안 쓰였습니다. "
                   f"지워도 됩니다 — ~/.claude/skills/{self.command}/ 폴더를 삭제하세요.")
        if self.verdict == "WORKING":
            return f"효과가 있습니다 (하루 발생률 {self.drop}% 감소, 사용 {self.used}회). 계속 쓰세요."
        return (f"효과가 뚜렷하지 않습니다 (거의 그대로, 사용 {self.used}회). "
               f"지워도 됩니다 — ~/.claude/skills/{self.command}/ 폴더를 삭제하세요.")


def evaluate(records: Sequence[Mapping], now: datetime | None = None) -> list[Effect]:
    now = now or datetime.now(timezone.utc)
    promotions = _latest_per_command(r for r in records if r.get("kind") == "promotion")
    turns = [r for r in records if r.get("kind") == "turn"]

    effects = []
    for promo in promotions:
        promoted_at = _parse(promo["ts"])
        if promoted_at is None:
            continue
        command = promo["command"]
        codes = tuple(promo.get("codes") or ())
        days_since = max((now - promoted_at).days, 0)

        baseline_days = int(promo.get("baseline_days") or 30) or 30
        baseline_per_day = int(promo.get("baseline_count") or 0) / baseline_days

        after_count = sum(
            1 for r in turns
            for signal in r.get("signals") or []
            if signal.get("code") in codes and _after(r.get("ts"), promoted_at)
        )
        current_per_day = after_count / days_since if days_since > 0 else 0.0

        used = sum(
            1 for r in turns
            if command in (r.get("skills") or []) and _after(r.get("ts"), promoted_at)
        )

        if days_since < MIN_OBSERVATION_DAYS:
            verdict = "TOO_SOON"
        elif days_since >= UNUSED_MIN_DAYS and used <= UNUSED_MAX_USES:
            verdict = "UNUSED"
        elif baseline_per_day <= 0 or current_per_day <= baseline_per_day * WORKING_THRESHOLD:
            verdict = "WORKING"
        else:
            verdict = "NOT_WORKING"

        effects.append(Effect(command, codes, days_since, baseline_per_day,
                              current_per_day, used, verdict))
    return effects


def render(effects: Sequence[Effect]) -> str:
    if not effects:
        return ""
    lines = ["## 승격한 명령어, 효과가 있었나", ""]
    for effect in sorted(effects, key=lambda e: e.command):
        lines.append(f"- `/{effect.command}` — {effect.recommendation}")
    return "\n".join(lines) + "\n"


# --- implementation ------------------------------------------------------


def _latest_per_command(promotions: Sequence[Mapping]) -> list[Mapping]:
    """A command re-promoted after its rules were edited should be judged
    from its newest baseline, not a stale one."""
    latest: dict[str, Mapping] = {}
    for promo in promotions:
        command = promo.get("command")
        if not command:
            continue
        if command not in latest or str(promo.get("ts", "")) > str(latest[command].get("ts", "")):
            latest[command] = promo
    return list(latest.values())


def _parse(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _after(stamp: object, cutoff: datetime) -> bool:
    parsed = _parse(stamp)
    return parsed is not None and parsed >= cutoff
