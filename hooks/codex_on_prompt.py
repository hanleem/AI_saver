#!/usr/bin/env python3
"""Codex CLI UserPromptSubmit hook.

Same judgment as ``on_prompt.py`` -- ``ai_saver.gate.assess``
takes only prompt text, so it is the same function either way. Only the
transport differs:

  * Codex's UserPromptSubmit payload uses ``prompt`` / ``turn_id``, not
    Claude Code's ``user_prompt`` / ``prompt_id``.
  * Codex accepts plain text on stdout as extra developer context -- no
    JSON envelope needed, unlike Claude Code's ``hookSpecificOutput``.

Session aggregation is handled separately by ``codex_on_session_end.py``.
This hook only makes the zero-model-call scope decision before work begins.

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

    from ai_saver import optionwiki
    from ai_saver.gate import assess
    from ai_saver.ledger import Ledger, gate_record
    from ai_saver.profile import Profile

    profile = Profile.load()
    # The Codex branch keeps its personal wiki under ~/.codex/ai-saver.
    verdict = assess(prompt, profile, options=optionwiki.load())

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
