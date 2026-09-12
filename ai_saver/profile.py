"""What AI_saver has learned about one person, and where its data lives.

Interface
---------
    data_root()                  -> Path
    Profile.load(root)           -> Profile
    profile.save(root)           -> None
    profile.calibrated(scores)   -> Profile   (pure; returns a new Profile)

The calibration rule is the product's safety catch. A coach that interrupts
too often gets switched off, and a switched-off coach saves nothing, so the
threshold moves until interruptions stay under ``target_high_rate``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

__all__ = ["Profile", "data_root"]

_FLOOR, _CEILING = 30, 95


def data_root() -> Path:
    """Where the ledger, profile and reports live.

    One place for every project. Waste patterns are properties of a person,
    not of a repository -- someone who never names a file does it everywhere --
    and each record carries its own ``cwd`` when a per-project view is wanted.
    """
    override = os.environ.get("AI_SAVER_HOME")
    return Path(override) if override else Path.home() / ".claude" / "ai-saver"


@dataclass(frozen=True)
class Profile:
    threshold: int = 45
    gate_enabled: bool = False  # ships in shadow mode: judge, record, don't interrupt
    target_high_rate: float = 0.10
    store_prompts: bool = False  # public build: keep hashes, not the words
    updated: str = ""
    version: int = 1

    @classmethod
    def load(cls, root: Path | None = None) -> "Profile":
        path = (root or data_root()) / "profile.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        fields = {f: raw[f] for f in cls.__dataclass_fields__ if f in raw}
        try:
            return cls(**fields)
        except TypeError:
            return cls()

    def save(self, root: Path | None = None) -> None:
        path = (root or data_root()) / "profile.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamped = replace(self, updated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        path.write_text(json.dumps(asdict(stamped), indent=2, ensure_ascii=False), encoding="utf-8")

    def calibrated(self, scores: Sequence[int]) -> "Profile":
        """Move the threshold so that at most ``target_high_rate`` of prompts
        are interrupted. Half-steps, so one noisy week cannot swing it."""
        if len(scores) < 20:
            return self
        ranked = sorted(scores, reverse=True)
        cut = max(1, round(len(ranked) * self.target_high_rate)) - 1
        wanted = max(_FLOOR, min(_CEILING, int(ranked[cut])))
        moved = int(round((self.threshold + wanted) / 2))
        return replace(self, threshold=max(_FLOOR, min(_CEILING, moved)))
