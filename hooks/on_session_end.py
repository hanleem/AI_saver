#!/usr/bin/env python3
"""SessionEnd hook: read the finished transcript, write what it cost.

Runs after the conversation, so the async-write lag noted in the hook docs
is no longer a problem. Costs zero tokens: it is a file read, not a turn.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    payload = json.load(sys.stdin)
    transcript = payload.get("transcript_path")
    if not transcript:
        return

    from ai_saver.ledger import Ledger, turn_record
    from ai_saver.profile import Profile
    from ai_saver.signals import detect
    from ai_saver.transcript import read_turns

    turns = read_turns(Path(transcript))
    if not turns:
        return

    decisions = {t.prompt_id: t.choices[0] for t in turns if t.choices and t.prompt_id}
    findings = detect(turns, decisions)

    records = [turn_record(turn, findings) for turn in turns if turn.prompt_id]
    records += [
        {"v": 1, "kind": "choice", "id": prompt_id, "choice": choice,
         "ts": next((t.started_at.isoformat(timespec="seconds")
                     for t in turns if t.prompt_id == prompt_id and t.started_at), "")}
        for prompt_id, choice in decisions.items()
    ]
    Ledger(profile=Profile.load()).append(records)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
