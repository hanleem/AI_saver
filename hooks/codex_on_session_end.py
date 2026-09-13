#!/usr/bin/env python3
"""Codex SessionEnd hook: aggregate the completed local transcript."""

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

    from ai_saver.codex_transcript import read_codex_turns
    from ai_saver.ledger import Ledger, turn_record
    from ai_saver.profile import Profile
    from ai_saver.signals import detect

    turns = read_codex_turns(Path(transcript))
    if not turns:
        return
    findings = detect(turns, {})
    Ledger(profile=Profile.load()).append(
        turn_record(turn, findings) for turn in turns if turn.prompt_id
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Hooks must never prevent Codex from closing a session.
        pass
    sys.exit(0)
