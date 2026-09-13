"""The durable record of what happened.

Interface
---------
    Ledger(root, profile).append(records) -> int
    Ledger(...).month(ym)                 -> list[dict]
    Ledger(...).months()                  -> list[str]
    Ledger(...).decisions(ym)             -> dict[prompt_id, option]
    Ledger(...).all_records()             -> list[dict]
    turn_record(turn, findings)           -> dict
    gate_record(prompt_id, verdict, ...)  -> dict
    promotion_record(skill, baseline)     -> dict

Append is idempotent: re-running a backfill over the same transcripts must
not double-count, and a session that is analysed twice must not grow the
ledger. Callers therefore never have to track what they have already written.

Redaction happens here, on the way in, so there is no window in which raw
prompts sit on disk.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .profile import Profile, data_root
from .signals import Finding
from .transcript import BUILD, EDIT, READ, Turn

__all__ = ["Ledger", "turn_record", "gate_record", "promotion_record"]

SCHEMA = 1


class Ledger:
    def __init__(self, root: Path | None = None, profile: Profile | None = None) -> None:
        self._root = (root or data_root()) / "ledger"
        self._profile = profile if profile is not None else Profile.load(root)

    def append(self, records: Iterable[Mapping]) -> int:
        by_month: dict[str, list[dict]] = {}
        for record in records:
            record = self._redact(dict(record))
            by_month.setdefault(_month_of(record), []).append(record)

        written = 0
        for month, batch in by_month.items():
            known = {(r.get("kind"), r.get("id")) for r in self.month(month)}
            fresh = [r for r in batch if (r.get("kind"), r.get("id")) not in known]
            if not fresh:
                continue
            path = self._path(month)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for record in fresh:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += len(fresh)
        return written

    def month(self, month: str) -> list[dict]:
        path = self._path(month)
        if not path.exists():
            return []
        records = []
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def months(self) -> list[str]:
        if not self._root.exists():
            return []
        return sorted(p.stem for p in self._root.glob("*.jsonl"))

    def all_records(self) -> list[dict]:
        """Every record, every month. Effect evaluation and promotion
        eligibility both need to look further back than one calendar month."""
        return [record for month in self.months() for record in self.month(month)]

    def decisions(self, month: str) -> dict[str, str]:
        """Which option the person actually picked, per prompt."""
        return {
            r["id"]: r["choice"]
            for r in self.month(month)
            if r.get("kind") == "gate" and r.get("choice") and r.get("id")
        }

    # --- implementation --------------------------------------------------

    def _path(self, month: str) -> Path:
        return self._root / f"{month}.jsonl"

    def _redact(self, record: dict) -> dict:
        prompt = record.pop("prompt", "")
        if prompt:
            record["prompt_hash"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
            record["prompt_len"] = len(prompt)
            if self._profile.store_prompts:
                record["prompt"] = prompt
        return record


def turn_record(turn: Turn, findings: Sequence[Finding] = ()) -> dict:
    mine = [f for f in findings if f.prompt_id == turn.prompt_id] if turn.prompt_id else []
    return {
        "v": SCHEMA,
        "kind": "turn",
        "id": turn.prompt_id,
        "ts": (turn.started_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "session": turn.session_id,
        "cwd": turn.cwd,
        "cost": round(turn.cost, 1),
        "input": turn.tokens.input,
        "cache_creation": turn.tokens.cache_creation,
        "cache_read": turn.tokens.cache_read,
        "output": turn.tokens.output,
        "calls": len(turn.calls),
        "reads": len(turn.targets(READ)),
        "edits": len(turn.targets(EDIT)),
        "builds": len(turn.targets(BUILD)),
        "skills": sorted(turn.skills_used),
        "seconds": round(turn.seconds, 1),
        "signals": [
            {"code": f.code, "detail": f.detail, "wasted": round(f.wasted, 1)} for f in mine
        ],
        "prompt": turn.prompt,
    }


def gate_record(prompt_id: str, verdict, session_id: str = "", cwd: str = "",
                choice: str = "", prompt: str = "") -> dict:
    return {
        "v": SCHEMA,
        "kind": "gate",
        "id": prompt_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session_id,
        "cwd": cwd,
        "level": verdict.level,
        "score": verdict.score,
        "task": verdict.task,
        "reasons": list(verdict.reasons),
        "recommended": verdict.recommended,
        "choice": choice,
        "prompt": prompt,
    }


def promotion_record(command: str, codes: Sequence[str], baseline_count: int,
                     baseline_days: int) -> dict:
    """Marks the moment a habit became a skill, with the rate that justified
    it. Without this, nothing could later tell whether the skill actually
    changed the habit -- there would be no "before" to compare "after" to."""
    return {
        "v": SCHEMA,
        "kind": "promotion",
        "id": command,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": command,
        "codes": list(codes),
        "baseline_count": baseline_count,
        "baseline_days": baseline_days,
    }


def _month_of(record: Mapping) -> str:
    stamp = str(record.get("ts") or "")
    return stamp[:7] if len(stamp) >= 7 else datetime.now(timezone.utc).strftime("%Y-%m")
