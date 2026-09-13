#!/usr/bin/env python3
"""Codex CLI UserPromptSubmit hook.

Same judgment as ``on_prompt.py`` (Claude Code) -- ``ai_saver.gate.assess``
takes only prompt text, so it is the same function either way. Only the
transport differs:

  * Codex's UserPromptSubmit payload uses ``prompt`` / ``turn_id``, not
    Claude Code's ``user_prompt`` / ``prompt_id``.
  * Codex accepts plain text on stdout as extra developer context -- no
    JSON envelope needed, unlike Claude Code's ``hookSpecificOutput``.

AI_saver's Codex support stops here, deliberately. Codex's session JSONL
(``~/.codex/sessions/.../rollout-*.jsonl``) has a schema this project has
not verified closely enough to parse with confidence, and interactive
sessions are documented as sometimes omitting token-count events entirely.
Shipping a guessed parser would risk silently wrong numbers, which is worse
than not shipping one -- so the monthly habit report stays Claude-Code-only
for now. This hook still records every verdict to the shared ledger, so
``calibrate`` (which only needs gate scores, not token counts) works the
same for Codex users.

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
    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) < MIN_LENGTH:
        return

    from ai_saver.gate import assess
    from ai_saver.ledger import Ledger, gate_record
    from ai_saver.profile import Profile

    profile = Profile.load()
    verdict = assess(prompt, profile)

    try:
        Ledger(profile=profile).append([
            gate_record(
                prompt_id=payload.get("turn_id") or "",
                verdict=verdict,
                session_id=payload.get("session_id") or "",
                cwd=payload.get("cwd") or "",
                prompt=prompt,
            )
        ])
    except OSError:
        pass

    if profile.gate_enabled and verdict.intervene:
        # Plain text on stdout -- Codex's contract, no JSON wrapper needed.
        print(verdict.as_context())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
