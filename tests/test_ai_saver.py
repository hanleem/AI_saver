"""Tests through each module's interface -- never past it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_saver import effect, optionwiki  # noqa: E402
from ai_saver.gate import DEFAULT_OPTIONS, Option, assess  # noqa: E402
from ai_saver.ledger import Ledger, promotion_record, turn_record  # noqa: E402
from ai_saver.profile import Profile  # noqa: E402
from ai_saver.promotion import (  # noqa: E402
    PURPOSE, Skill, group_by_command, render_skill, skills_root,
)
from ai_saver.report import SKILL_MIN, habit_counts, render_month  # noqa: E402
from ai_saver.signals import detect  # noqa: E402
from ai_saver.transcript import BUILD, EDIT, READ, ToolCall, Turn, read_turns  # noqa: E402

USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 1000,
    "output_tokens": 50,
    "output_tokens_details": {"thinking_tokens": 5},
}


def _prompt(pid: str, text: str, uuid: str) -> dict:
    return {"type": "user", "promptId": pid, "uuid": uuid, "sessionId": "s1",
            "cwd": "C:/proj", "timestamp": "2026-09-01T10:00:00Z",
            "message": {"role": "user", "content": text}}


def _assistant(request_id: str, blocks: list[dict]) -> dict:
    return {"type": "assistant", "requestId": request_id, "sessionId": "s1",
            "cwd": "C:/proj", "timestamp": "2026-09-01T10:01:00Z",
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "usage": USAGE, "content": blocks}}


def _tool(name: str, args: dict, tool_id: str = "t1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _write(lines: list[dict]) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    with handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    return Path(handle.name)


class TranscriptTest(unittest.TestCase):
    def test_usage_counted_once_per_request(self):
        """One API response spans several lines that each repeat the usage."""
        path = _write([
            _prompt("p1", "로그인 화면 고쳐줘", "u1"),
            _assistant("r1", [{"type": "thinking", "thinking": "..."}]),
            _assistant("r1", [{"type": "text", "text": "네"}]),
            _assistant("r1", [_tool("Read", {"file_path": "C:/proj/a.py"})]),
        ])
        turn, = read_turns(path)
        self.assertEqual(turn.tokens.input, 10)
        self.assertEqual(turn.tokens.cache_read, 1000)
        self.assertAlmostEqual(turn.cost, 10 + 100 * 1.25 + 1000 * 0.1 + 50)
        self.assertEqual(len(turn.calls), 1)

    def test_tool_results_do_not_start_a_turn(self):
        path = _write([
            _prompt("p1", "로그인 화면 고쳐줘", "u1"),
            _assistant("r1", [_tool("Read", {"file_path": "a.py"})]),
            {"type": "user", "uuid": "u2", "toolUseResult": {"ok": True},
             "message": {"role": "user", "content": [{"type": "tool_result",
                                                      "tool_use_id": "t1", "content": "x"}]}},
            _prompt("p2", "이번엔 버튼만", "u3"),
        ])
        turns = read_turns(path)
        self.assertEqual([t.prompt_id for t in turns], ["p1", "p2"])

    def test_shell_commands_are_classified(self):
        path = _write([
            _prompt("p1", "빌드 돌려줘", "u1"),
            _assistant("r1", [
                _tool("Bash", {"command": "cat src/app.py"}, "t1"),
                _tool("Bash", {"command": "npm run build"}, "t2"),
                _tool("Bash", {"command": "sed -i s/a/b/ src/app.py"}, "t3"),
            ]),
        ])
        turn, = read_turns(path)
        self.assertEqual([c.kind for c in turn.calls], [READ, BUILD, EDIT])
        self.assertEqual(turn.targets(READ), ["src/app.py"])

    def test_gate_answer_is_captured(self):
        path = _write([
            _prompt("p1", "전체 디자인 바꿔줘", "u1"),
            _assistant("r1", [_tool("AskUserQuestion", {"questions": []}, "q1")]),
            {"type": "user", "uuid": "u2", "toolUseResult": "A. 최소 수정",
             "message": {"role": "user", "content": [{"type": "tool_result",
                                                      "tool_use_id": "q1",
                                                      "content": "A. 최소 수정"}]}},
        ])
        turn, = read_turns(path)
        self.assertEqual(turn.choices, ["A"])

    def test_malformed_lines_are_survivable(self):
        path = _write([_prompt("p1", "고쳐줘 이것 좀 전체적으로", "u1")])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "assistant", "truncated\n')
        self.assertEqual(len(read_turns(path)), 1)


class GateTest(unittest.TestCase):
    def setUp(self):
        self.profile = Profile()

    def test_open_ended_prompt_is_gated(self):
        verdict = assess("전체적으로 확인해서 문제 있으면 다 고쳐줘", self.profile)
        self.assertTrue(verdict.intervene)
        self.assertEqual(len(verdict.options), 4)
        self.assertIn(verdict.recommended, "ABCD")

    def test_named_file_is_not_gated(self):
        verdict = assess("login.tsx 의 버튼 색만 파란색으로 바꿔줘", self.profile)
        self.assertFalse(verdict.intervene)

    def test_question_recommends_planning_only(self):
        verdict = assess("전체 구조가 어떻게 되어 있는지 알려줘", self.profile)
        self.assertEqual(verdict.recommended, "D")

    def test_task_picks_its_own_options(self):
        design = assess("전체 앱 디자인 다 예쁘게 바꿔줘", self.profile)
        bug = assess("전체적으로 에러가 나는데 다 고쳐줘", self.profile)
        self.assertEqual(design.task, "design")
        self.assertEqual(bug.task, "bug")
        self.assertNotEqual(design.options[2].label, bug.options[2].label)

    def test_injected_context_stays_small(self):
        verdict = assess("전체적으로 확인해서 문제 있으면 다 고쳐줘", self.profile)
        context = verdict.as_context()
        self.assertLess(len(context), 700)
        self.assertNotIn("AskUserQuestion", context)  # Claude-only tool name; text must stay tool-agnostic
        self.assertIn(verdict.recommended, context)

    def test_omitting_options_keeps_assess_pure_and_unchanged(self):
        """No caller that never heard of the wiki should see any difference --
        this is the whole reason assess() stays disk-free by default."""
        without = assess("전체 앱 디자인 다 예쁘게 바꿔줘", self.profile)
        with_default = assess("전체 앱 디자인 다 예쁘게 바꿔줘", self.profile, options=DEFAULT_OPTIONS)
        self.assertEqual(without.options, with_default.options)

    def test_a_wiki_edit_actually_changes_what_the_gate_shows(self):
        """The end-to-end point of the wiki: edit the file, no redeploy,
        the very next assess() call reflects it."""
        edited = dict(DEFAULT_OPTIONS)
        edited["code"] = (Option("A", "개정판 문구", "★", "규칙"),) + DEFAULT_OPTIONS["code"][1:]
        verdict = assess("전체 코드 다 정리해줘", self.profile, options=edited)
        self.assertEqual(verdict.options[0].label, "개정판 문구")


class OptionWikiTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_first_use_seeds_the_file_from_defaults(self):
        path = optionwiki.ensure(self.root)
        self.assertTrue(path.exists())
        self.assertEqual(optionwiki.load(self.root), DEFAULT_OPTIONS)

    def test_render_then_parse_round_trips_exactly(self):
        text = optionwiki.render(DEFAULT_OPTIONS)
        self.assertEqual(optionwiki.parse(text), DEFAULT_OPTIONS)

    def test_table_of_contents_lists_every_task_up_front(self):
        text = optionwiki.render(DEFAULT_OPTIONS)
        toc_position = text.index("## 목차")
        for task in DEFAULT_OPTIONS:
            self.assertIn(f"[{task}](#{task})", text)
            self.assertGreater(text.index(f"[{task}](#{task})"), toc_position)
            self.assertLess(text.index(f"[{task}](#{task})"), text.index(f"## {task}"))

    def test_editing_one_task_leaves_the_others_on_defaults(self):
        path = optionwiki.ensure(self.root)
        text = path.read_text(encoding="utf-8").replace(
            "지정한 파일만 고치기", "새 문구")
        path.write_text(text, encoding="utf-8")

        table = optionwiki.load(self.root)
        self.assertEqual(table["code"][0].label, "새 문구")
        self.assertEqual(table["design"], DEFAULT_OPTIONS["design"])

    def test_one_broken_row_falls_back_for_that_task_only_not_a_three_option_gate(self):
        """Never let a four-option gate quietly become a three-option one --
        but a typo in 'code' must not also reset the untouched 'design' section."""
        path = optionwiki.ensure(self.root)
        text = path.read_text(encoding="utf-8").replace(
            "| A | 지정한 파일만 고치기 | ★ | 사용자가 지목한 파일만 읽고 수정한다. 다른 파일은 열지 않는다. |",
            "그냥 사람이 아무렇게나 쓴 문장, 표 형식이 아님")
        path.write_text(text, encoding="utf-8")

        table = optionwiki.load(self.root)
        self.assertEqual(table["code"], DEFAULT_OPTIONS["code"])  # whole task reverts, not 3/4 of it
        self.assertEqual(table["design"], DEFAULT_OPTIONS["design"])  # untouched task unaffected

    def test_completely_broken_file_falls_back_entirely(self):
        path = optionwiki.ensure(self.root)
        path.write_text("완전히 망가진 내용, 표도 없고 헤딩도 없음", encoding="utf-8")
        self.assertEqual(optionwiki.load(self.root), DEFAULT_OPTIONS)

    def test_reset_discards_manual_edits(self):
        path = optionwiki.ensure(self.root)
        path.write_text(path.read_text(encoding="utf-8").replace("★", "★★★★★★★★★★"),
                        encoding="utf-8")
        optionwiki.reset(self.root)
        self.assertEqual(optionwiki.load(self.root), DEFAULT_OPTIONS)


def _turn(prompt_id: str, prompt: str, calls: list[ToolCall], cost_calls: int = 1) -> Turn:
    from ai_saver.transcript import TokenUse
    return Turn(prompt_id=prompt_id, session_id="s1", prompt=prompt,
                tokens=TokenUse(output=1000 * cost_calls), calls=calls)


class SignalsTest(unittest.TestCase):
    def _codes(self, turns, decisions=None):
        return {f.code for f in detect(turns, decisions)}

    def test_rediscovery(self):
        calls = [ToolCall("Read", READ, "a.py")] * 3
        self.assertIn("REDISCOVERY", self._codes([_turn("p1", "고쳐", calls)]))

    def test_build_loop(self):
        calls = [ToolCall("Bash", BUILD, "npm run build")] * 3
        self.assertIn("BUILD_LOOP", self._codes([_turn("p1", "고쳐", calls)]))

    def test_scope_blowup(self):
        calls = [ToolCall("Read", READ, f"f{i}.py") for i in range(30)]
        calls.append(ToolCall("Edit", EDIT, "a.py"))
        self.assertIn("SCOPE_BLOWUP", self._codes([_turn("p1", "고쳐", calls)]))

    def test_context_repeat(self):
        text = "이 프로젝트는 리액트로 만든 쇼핑몰이고 로그인 화면을 고치려고 합니다 도와주세요"
        turns = [_turn("p1", text, []), _turn("p2", text + " 부탁", [])]
        self.assertIn("CONTEXT_REPEAT", self._codes(turns))

    def test_underscoped_fail_needs_the_choice(self):
        text = "로그인 화면의 저장 버튼 정렬이 안 맞는데 고쳐줘 지금"
        turns = [_turn("p1", text, []), _turn("p2", text + " 다시", [])]
        self.assertNotIn("UNDERSCOPED_FAIL", self._codes(turns))
        self.assertIn("UNDERSCOPED_FAIL", self._codes(turns, {"p1": "A"}))

    def test_clean_work_is_quiet(self):
        calls = [ToolCall("Read", READ, "a.py"), ToolCall("Edit", EDIT, "a.py")]
        self.assertEqual(self._codes([_turn("p1", "a.py 의 버튼만 고쳐줘", calls)]), set())


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def _ledger(self, **kwargs):
        return Ledger(root=self.root, profile=Profile(**kwargs))

    def test_append_is_idempotent(self):
        record = turn_record(_turn("p1", "고쳐줘", []))
        self.assertEqual(self._ledger().append([record]), 1)
        self.assertEqual(self._ledger().append([record]), 0)
        self.assertEqual(len(self._ledger().month(record["ts"][:7])), 1)

    def test_prompts_are_hashed_by_default(self):
        self._ledger().append([turn_record(_turn("p1", "비밀 프로젝트 이름", []))])
        stored, = self._ledger().month(_month())
        self.assertNotIn("prompt", stored)
        self.assertEqual(len(stored["prompt_hash"]), 12)
        self.assertEqual(stored["prompt_len"], len("비밀 프로젝트 이름"))

    def test_prompts_kept_when_opted_in(self):
        self._ledger(store_prompts=True).append([turn_record(_turn("p1", "보관해도 됨", []))])
        stored, = self._ledger().month(_month())
        self.assertEqual(stored["prompt"], "보관해도 됨")


class ProfileTest(unittest.TestCase):
    def test_calibration_targets_the_interruption_rate(self):
        tuned = Profile(threshold=45).calibrated(list(range(100)))
        self.assertEqual(tuned.threshold, 68)

    def test_small_samples_do_not_move_it(self):
        self.assertEqual(Profile().calibrated([90] * 5).threshold, 45)

    def test_round_trip(self):
        root = Path(tempfile.mkdtemp())
        Profile(threshold=61, gate_enabled=True).save(root)
        loaded = Profile.load(root)
        self.assertEqual((loaded.threshold, loaded.gate_enabled), (61, True))


class CodexHookTest(unittest.TestCase):
    """The hook actually run by Codex CLI, invoked as a real subprocess --
    the process boundary and the stdin/stdout contract are the point."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.script = Path(__file__).resolve().parent.parent / "hooks" / "codex_on_prompt.py"
        self.env = {**os.environ, "AI_SAVER_HOME": str(self.home), "PYTHONIOENCODING": "utf-8"}

    def _run(self, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.script)],
            input=json.dumps(payload), capture_output=True, text=True,
            encoding="utf-8", env=self.env,
        )

    def test_shadow_mode_is_silent_but_still_records(self):
        result = self._run({"prompt": "전체 앱 디자인 다 예쁘게 바꿔줘", "turn_id": "t1",
                            "session_id": "s1", "cwd": "/proj"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        stored, = Ledger(root=self.home, profile=Profile()).month(_month())
        self.assertEqual(stored["kind"], "gate")

    def test_gate_on_prints_plain_text_not_json(self):
        Profile(gate_enabled=True).save(self.home)
        result = self._run({"prompt": "전체 앱 디자인 다 예쁘게 바꿔줘", "turn_id": "t2",
                            "session_id": "s1", "cwd": "/proj"})
        self.assertNotIn("hookSpecificOutput", result.stdout)  # that's Claude Code's envelope, not Codex's
        self.assertIn("A.", result.stdout)

    def test_malformed_input_never_breaks_the_turn(self):
        result = subprocess.run([sys.executable, str(self.script)], input="not json",
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0)


class WikiCliTest(unittest.TestCase):
    """The actual command a person types, run as a real subprocess."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.wiki_root = Path(tempfile.mkdtemp())
        self.cli = Path(__file__).resolve().parent.parent / "scripts" / "ai_saver_cli.py"
        self.env = {**os.environ, "AI_SAVER_HOME": str(self.home), "PYTHONIOENCODING": "utf-8"}

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.cli), *args],
                              capture_output=True, text=True, encoding="utf-8", env=self.env)

    def test_show_seeds_and_prints_the_table_of_contents(self):
        result = self._run("wiki", "--root", str(self.wiki_root))
        self.assertEqual(result.returncode, 0)
        self.assertIn("## 목차", result.stdout)
        self.assertIn("[code](#code)", result.stdout)

    def test_reset_restores_a_manually_edited_file(self):
        self._run("wiki", "--root", str(self.wiki_root))  # seed it first
        wiki_file = self.wiki_root / "wiki" / "options.md"
        wiki_file.write_text("사람이 손으로 다 지우고 새로 씀", encoding="utf-8")

        result = self._run("wiki", "reset", "--root", str(self.wiki_root))
        self.assertEqual(result.returncode, 0)
        self.assertIn("## 목차", wiki_file.read_text(encoding="utf-8"))

    def test_stats_without_data_does_not_crash(self):
        result = self._run("wiki", "stats")
        self.assertEqual(result.returncode, 0)
        self.assertIn("아직", result.stdout)

    def test_stats_shows_recommendation_vs_choice_by_task(self):
        records = [
            {"v": 1, "kind": "gate", "id": "p1", "ts": "2026-09-01T00:00:00+00:00",
             "task": "design", "recommended": "B", "choice": "C"},
            {"v": 1, "kind": "gate", "id": "p2", "ts": "2026-09-02T00:00:00+00:00",
             "task": "design", "recommended": "B", "choice": "B"},
        ]
        Ledger(root=self.home, profile=Profile()).append(records)
        result = self._run("wiki", "stats")
        self.assertIn("design", result.stdout)
        self.assertIn("50%", result.stdout)  # 1 of 2 answers disagreed with the recommendation


class PromoteCliTest(unittest.TestCase):
    """The actual command a person types, run as a real subprocess."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.skills = Path(tempfile.mkdtemp())
        self.cli = Path(__file__).resolve().parent.parent / "scripts" / "ai_saver_cli.py"
        self.env = {**os.environ, "AI_SAVER_HOME": str(self.home), "PYTHONIOENCODING": "utf-8"}

    def _seed(self, code: str, count: int) -> None:
        records = [turn_record(_turn(f"p{code}{i}", "x", [])) for i in range(count)]
        for r in records:
            r["signals"] = [{"code": code, "detail": "", "wasted": 0}]
        Ledger(root=self.home, profile=Profile()).append(records)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.cli), *args],
                              capture_output=True, text=True, encoding="utf-8", env=self.env)

    def test_below_threshold_refuses(self):
        self._seed("REDISCOVERY", SKILL_MIN - 1)
        result = self._run("promote", "focus-file", "--root", str(self.skills))
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.skills / "focus-file").exists())

    def test_at_threshold_creates_the_real_file(self):
        self._seed("REDISCOVERY", SKILL_MIN)
        result = self._run("promote", "focus-file", "--root", str(self.skills))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.skills / "focus-file" / "SKILL.md").exists())
        self.assertIn("focus-file", result.stdout)

    def test_promoting_leaves_a_baseline_so_effect_can_be_judged_later(self):
        self._seed("REDISCOVERY", SKILL_MIN)
        self._run("promote", "focus-file", "--root", str(self.skills))
        promotions = [r for r in Ledger(root=self.home, profile=Profile()).all_records()
                     if r.get("kind") == "promotion"]
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["command"], "focus-file")
        self.assertEqual(promotions[0]["baseline_count"], SKILL_MIN)

    def test_unknown_name_lists_what_exists_instead(self):
        result = self._run("promote", "not-a-real-thing", "--root", str(self.skills))
        self.assertEqual(result.returncode, 1)
        self.assertIn("focus-file", result.stdout)


class ReportTest(unittest.TestCase):
    def test_no_raw_token_numbers_for_beginners(self):
        records = [turn_record(_turn("p1", "고쳐줘", [ToolCall("Read", READ, "a.py")] * 3))]
        records[0]["signals"] = [{"code": "REDISCOVERY", "detail": "a.py — 3번 읽음", "wasted": 500}]
        text = render_month(_month(), records)
        self.assertNotIn("cache_read", text)
        self.assertNotIn("가중 토큰", text)
        self.assertIn("같은 파일", text)

    def test_technical_appendix_has_them(self):
        records = [turn_record(_turn("p1", "고쳐줘", []))]
        self.assertIn("가중 토큰", render_month(_month(), records, technical=True))

    def test_empty_month_says_what_to_do(self):
        self.assertIn("backfill", render_month("2026-09", []))

    def test_candidate_explains_what_it_would_do_and_that_it_is_not_real_yet(self):
        """The exact gap a real user hit: a bare name like /project-brief with
        no explanation and nothing actually created."""
        records = [turn_record(_turn(f"p{i}", "고쳐줘", [])) for i in range(5)]
        for r in records:
            r["signals"] = [{"code": "CONTEXT_REPEAT", "detail": "", "wasted": 0}]
        text = render_month(_month(), records)
        self.assertIn("project-brief", text)
        self.assertIn(PURPOSE["CONTEXT_REPEAT"].summary, text)
        self.assertIn("아직 만들어진 명령어가 아닙니다", text)

    def test_already_promoted_command_is_not_called_not_made_yet(self):
        """Would otherwise contradict the effect section right below it,
        which reads the same ledger and says the skill already exists."""
        records = [turn_record(_turn(f"p{i}", "x", [])) for i in range(5)]
        for r in records:
            r["signals"] = [{"code": "CONTEXT_REPEAT", "detail": "", "wasted": 0}]
        text = render_month(_month(), records, promoted=frozenset({"project-brief"}))
        self.assertNotIn("project-brief` (아직 없음", text)
        self.assertIn("이미 명령어로 만들어져", text)


def _promo(command: str, codes: list[str], baseline_count: int, days_ago: int,
          baseline_days: int = 30) -> dict:
    record = promotion_record(command, codes, baseline_count, baseline_days)
    record["ts"] = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return record


def _turns_at(code: str, count: int, days_ago: int) -> list[dict]:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    records = []
    for i in range(count):
        r = turn_record(_turn(f"{code}{days_ago}-{i}", "x", []))
        r["ts"] = ts
        r["signals"] = [{"code": code, "detail": "", "wasted": 0}]
        records.append(r)
    return records


class EffectTest(unittest.TestCase):
    def test_fresh_promotion_is_too_soon_to_judge(self):
        records = [_promo("focus-file", ["REDISCOVERY"], baseline_count=30, days_ago=2)]
        result, = effect.evaluate(records)
        self.assertEqual(result.verdict, "TOO_SOON")

    def test_habit_that_kept_happening_is_not_working(self):
        # baseline: 30 over 30 days = 1/day. Same rate continues after promotion.
        records = [_promo("focus-file", ["REDISCOVERY"], baseline_count=30, days_ago=20)]
        records += _turns_at("REDISCOVERY", 20, days_ago=10)  # ~1/day since promotion too
        result, = effect.evaluate(records)
        self.assertEqual(result.verdict, "NOT_WORKING")
        self.assertIn("지워도 됩니다", result.recommendation)

    def test_habit_that_nearly_stopped_is_working(self):
        records = [_promo("focus-file", ["REDISCOVERY"], baseline_count=30, days_ago=20)]
        records += _turns_at("REDISCOVERY", 1, days_ago=10)  # almost nothing since
        result, = effect.evaluate(records)
        self.assertEqual(result.verdict, "WORKING")
        self.assertIn("계속 쓰세요", result.recommendation)

    def test_signals_before_promotion_do_not_count_against_it(self):
        records = [_promo("focus-file", ["REDISCOVERY"], baseline_count=30, days_ago=20)]
        records += _turns_at("REDISCOVERY", 50, days_ago=25)  # all BEFORE promotion
        result, = effect.evaluate(records)
        self.assertEqual(result.verdict, "WORKING")

    def test_reproposal_judges_from_the_newest_baseline(self):
        records = [
            _promo("focus-file", ["REDISCOVERY"], baseline_count=30, days_ago=40),
            _promo("focus-file", ["REDISCOVERY"], baseline_count=5, days_ago=20),
        ]
        result, = effect.evaluate(records)
        self.assertEqual(result.baseline_per_day, 5 / 30)

    def test_no_promotions_renders_nothing(self):
        self.assertEqual(effect.render(effect.evaluate([])), "")


class PromotionTest(unittest.TestCase):
    def test_shared_command_merges_without_duplicate_rules(self):
        skill = render_skill(["SCOPE_BLOWUP", "VAGUE_SCOPE"], {"SCOPE_BLOWUP": 4, "VAGUE_SCOPE": 3})
        self.assertEqual(skill.command, "scoped-edit")
        self.assertEqual(skill.occurrences, 7)
        self.assertEqual(len(skill.rules), len(set(skill.rules)))  # no duplicates

    def test_write_makes_a_real_autocompleting_file(self):
        root = Path(tempfile.mkdtemp())
        skill = render_skill(["REDISCOVERY"], {"REDISCOVERY": 6})
        path = skill.write(root)
        self.assertEqual(path, root / "focus-file" / "SKILL.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("name: focus-file", text)
        self.assertIn(PURPOSE["REDISCOVERY"].summary, text)

    def test_description_shown_in_autocomplete_says_why_not_just_what(self):
        """A bare rule sentence doesn't tell someone scanning `/` that this
        command exists because a real habit repeated a counted number of
        times -- that context is exactly what belongs next to the name."""
        skill = render_skill(["REDISCOVERY"], {"REDISCOVERY": 123})
        self.assertIn("123회", skill.description)
        self.assertIn(PURPOSE["REDISCOVERY"].summary, skill.description)
        self.assertIn(f"description: {skill.description}", skill.render())

    def test_default_root_is_the_folder_claude_code_watches(self):
        self.assertEqual(skills_root(), Path.home() / ".claude" / "skills")

    def test_budget_is_enforced_not_just_documented(self):
        oversized = Skill("x", "s", tuple(f"rule {i}" for i in range(80)), ("X",), 5)
        with self.assertRaises(ValueError):
            oversized.render()

    def test_description_budget_checks_the_actual_rendered_line(self):
        """The check must guard the composed description (with the count
        prefix), not just the raw summary -- that prefix is what actually
        ends up in the frontmatter line Claude Code reads."""
        borderline = Skill("x", "s" * 490, ("rule",), ("X",), 5)  # fits alone, not with the prefix
        with self.assertRaises(ValueError):
            borderline.render()

    def test_unknown_code_is_rejected(self):
        with self.assertRaises(ValueError):
            render_skill(["NOPE"], {})

    def test_grouping_matches_what_the_report_and_cli_both_use(self):
        groups = group_by_command(["REDISCOVERY", "SCOPE_BLOWUP", "VAGUE_SCOPE"])
        self.assertEqual(set(groups), {"focus-file", "scoped-edit"})
        self.assertEqual(set(groups["scoped-edit"]), {"SCOPE_BLOWUP", "VAGUE_SCOPE"})


class HabitCountsTest(unittest.TestCase):
    def test_counts_only_turn_records(self):
        records = [
            turn_record(_turn("p1", "x", [])),
            {"kind": "gate", "id": "g1"},
        ]
        records[0]["signals"] = [{"code": "BUILD_LOOP", "detail": "", "wasted": 0}]
        self.assertEqual(habit_counts(records), Counter({"BUILD_LOOP": 1}))


def _month() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
