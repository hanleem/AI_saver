"""AI_saver -- learn how one person actually works, then stop the waste.

Four layers, each with one job:

    transcript   what happened          (no model call)
    signals      what was wasteful      (no model call)
    gate         what will be expensive (no model call, runs before the turn)
    ledger       what we keep           (redacted on the way in)
    report       what to say about it   (plain language, for a beginner)

Measurement is free by construction. A model is involved once a month, to
interpret a table that is already built.
"""

from .gate import Verdict, assess
from .ledger import Ledger, gate_record, turn_record
from .profile import Profile, data_root
from .report import render_month
from .signals import Finding, detect
from .transcript import ToolCall, TokenUse, Turn, read_all_turns, read_turns, transcript_root

__version__ = "0.1.0"

__all__ = [
    "assess", "Verdict",
    "Ledger", "turn_record", "gate_record",
    "Profile", "data_root",
    "render_month",
    "detect", "Finding",
    "Turn", "TokenUse", "ToolCall", "read_turns", "read_all_turns", "transcript_root",
]
