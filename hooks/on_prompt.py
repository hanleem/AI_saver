#!/usr/bin/env python3
"""UserPromptSubmit hook: judge the prompt, and only sometimes speak.

Two hard rules, both from the platform:

  * ``UserPromptSubmit`` does not support ``permissionDecision`` -- that field
    is only for tool events. So the gate cannot "ask" by itself; it injects a
    short instruction and the agent asks with AskUserQuestion.
  * The transcript "is written asynchronously and may lag the in-memory
    conversation", so this hook must judge from the prompt text alone.

Failure policy: never break the user's turn. Any error exits 0 in silence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIN_LENGTH = 12


def main() -> None:
    payload = json.load(sys.stdin)
    prompt = (payload.get("user_prompt") or payload.get("user_input") or "").strip()
    if len(prompt) < MIN_LENGTH or prompt.startswith("/"):
        return

    from ai_saver.gate import assess
    from ai_saver.ledger import Ledger, gate_record
    from ai_saver.profile import Profile

    profile = Profile.load()
    verdict = assess(prompt, profile)

    # Recorded for every prompt, including the quiet ones: the score
    # distribution is what keeps the interruption rate calibrated.
    try:
        Ledger(profile=profile).append([
            gate_record(
                prompt_id=payload.get("prompt_id") or "",
                verdict=verdict,
                session_id=payload.get("session_id") or "",
                cwd=payload.get("cwd") or "",
                prompt=prompt,
            )
        ])
    except OSError:
        pass

    if not (profile.gate_enabled and verdict.intervene):
        return

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": verdict.as_context(),
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
