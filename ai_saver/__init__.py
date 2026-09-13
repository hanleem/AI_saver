"""AI_saver -- learn how one person actually works, then stop the waste.

Eight modules, each with one job. Everything through ``promotion`` runs
with zero model calls; a model enters only when a hook renders a verdict's
options for a person to read, or once a month to interpret a report.

    transcript   what happened                    (no model call)
    signals      what was wasteful                 (no model call)
    gate         what will be expensive            (no model call, runs before the turn)
    optionwiki   the editable wording gate shows    (no model call; a human/model edits it)
    ledger       what we keep                       (redacted on the way in)
    promotion    turning a habit into a real /command
    effect       did a promoted command earn its keep
    report       what to say about all of it        (plain language, for a beginner)

Measurement is free by construction: a model is involved only when a hook
renders gate.Verdict for a person to answer, or once a month to interpret
a report that is already built.
"""

from . import effect, optionwiki, promotion
from .gate import DEFAULT_OPTIONS, Option, Verdict, assess
from .ledger import Ledger, gate_record, promotion_record, turn_record
from .profile import Profile, data_root
from .report import SKILL_MIN, habit_counts, render_month
from .signals import CODES, Finding, detect
from .transcript import ToolCall, TokenUse, Turn, read_all_turns, read_turns, transcript_root

__version__ = "0.2.0"

__all__ = [
    "assess", "Verdict", "Option", "DEFAULT_OPTIONS",
    "Ledger", "turn_record", "gate_record", "promotion_record",
    "Profile", "data_root",
    "render_month", "habit_counts", "SKILL_MIN",
    "detect", "Finding", "CODES",
    "Turn", "TokenUse", "ToolCall", "read_turns", "read_all_turns", "transcript_root",
    "optionwiki", "promotion", "effect",
]
