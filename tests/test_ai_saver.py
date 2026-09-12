"""Tests through each module's interface -- never past it."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_saver.gate import assess  # noqa: E402
from ai_saver.ledger import Ledger, turn_record  # noqa: E402
from ai_saver.profile import Profile  # noqa: E402
from ai_saver.report import render_month  # noqa: E402
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
        self.assertLess(len(verdict.as_context()), 700)
        self.assertIn("AskUserQuestion", verdict.as_context())


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


def _month() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
