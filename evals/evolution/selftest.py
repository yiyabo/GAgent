#!/usr/bin/env python3
"""Offline self-test for the evolution harness (no network, no LLM, no SSH).

    python3 evals/evolution/selftest.py

Checks oracle helpers, check semantics, failure classification, budget
accounting and the composed driver script for every public scenario.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import controller  # noqa: E402
import driver  # noqa: E402
import fixtures as fx  # noqa: E402
import scenarios  # noqa: E402

KNOWN_CHECK_KINDS = {
    "run_ok", "no_fallback", "answer_nonempty", "number_line", "number_any", "terms_any", "terms_none",
    "tool_used", "turns_max", "file_exists", "no_new_files", "file_records", "consistent_count", "consistent_n50",
    "no_number_on_terms",
}


class FixtureTests(unittest.TestCase):
    def test_n50_matches_definition(self):
        self.assertEqual(fx.n50([2, 2, 2, 2, 2]), 2)
        self.assertEqual(fx.n50([10, 5, 3, 2]), 10)  # 10 alone reaches half of 20

    def test_gc_percent(self):
        self.assertEqual(fx.gc_percent("GGCC"), 100.0)
        self.assertAlmostEqual(fx.gc_percent("ACGT"), 50.0)

    def test_fasta_round_trip(self):
        records = [("a", "ACGT" * 30), ("b", "GGCC")]
        self.assertEqual(fx.parse_fasta(fx.to_fasta(records)), records)

    def test_generators_are_deterministic(self):
        first = scenarios.s01_seqkit_stats()["fixtures"]["contigs.fasta"]
        second = scenarios.s01_seqkit_stats()["fixtures"]["contigs.fasta"]
        self.assertEqual(first, second)


class NumberTests(unittest.TestCase):
    def test_numbers_in_handles_separators_and_signs(self):
        self.assertEqual(driver.numbers_in("1,234.5 bp and 2024 and -3"), [1234.5, 2024.0, -3.0])

    def test_numbers_do_not_attach_to_identifiers(self):
        self.assertEqual(driver.numbers_in("ctg01 and N50 is 4321"), [4321.0])

    def test_numbers_on_lines_requires_a_label_on_the_same_line(self):
        text = "保留 2 条序列\n无关数字 7\n"
        self.assertEqual(driver.numbers_on_lines(text, ["保留"]), [2.0])
        self.assertEqual(driver.numbers_on_lines(text, None), [2.0, 7.0])


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fasta = self.tmp / "out.fasta"
        self.fasta.write_text(">a\n" + "A" * 3100 + "\n>b\n" + "C" * 1200 + "\n>c\n" + "G" * 3300 + "\n")
        self.ctx = {
            "answers": ["共保留 2 条序列；N50 为 3100。\n- 无关数字 7"],
            "runs": [{"status": "succeeded", "error": None, "fallback": False, "bailout": False,
                      "tools_used": ["bio_tools"]}],
            "files": [{"rel": "out.fasta", "abs": str(self.fasta), "bytes": self.fasta.stat().st_size,
                       "mtime": self.fasta.stat().st_mtime}],
        }

    def test_number_line_accepts_labelled_value(self):
        result = driver.run_check({"kind": "number_line", "value": 2, "terms": ["保留", "条"]}, self.ctx)
        self.assertTrue(result["passed"])

    def test_number_line_rejects_unlabelled_value(self):
        result = driver.run_check({"kind": "number_line", "value": 7, "terms": ["保留", "条"]}, self.ctx)
        self.assertFalse(result["passed"])

    def test_consistent_count_respects_min_len_and_terms(self):
        check = {"kind": "consistent_count", "pattern": r"out\.fasta$", "format": "fasta", "min_len": 3000,
                 "terms": ["保留"]}
        self.assertTrue(driver.run_check(check, self.ctx)["passed"])

    def test_consistent_n50_uses_the_output_file(self):
        check = {"kind": "consistent_n50", "pattern": r"out\.fasta$", "terms": ["N50"]}
        self.assertTrue(driver.run_check(check, self.ctx)["passed"])

    def test_file_records_counts_fasta_records(self):
        check = {"kind": "file_records", "pattern": r"\.fasta$", "format": "fasta", "count": 3}
        self.assertTrue(driver.run_check(check, self.ctx)["passed"])

    def test_tool_used_and_terms(self):
        self.assertTrue(driver.run_check({"kind": "tool_used", "name": "bio_tools"}, self.ctx)["passed"])
        self.assertFalse(driver.run_check({"kind": "terms_none", "terms": ["保留"]}, self.ctx)["passed"])
        self.assertTrue(driver.run_check({"kind": "terms_any", "terms": ["N50"]}, self.ctx)["passed"])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            driver.run_check({"kind": "nope"}, self.ctx)


class ClassifyTests(unittest.TestCase):
    def test_failures_map_to_classes(self):
        result = {
            "status": "fail",
            "checks": [
                {"kind": "number_line", "passed": False, "label": "count", "observed": "x"},
                {"kind": "terms_none", "passed": False, "label": "no claim", "observed": "y"},
                {"kind": "tool_used", "passed": True, "label": "tool", "observed": "z"},
            ],
            "judge": [{"id": "clarity", "score": 1, "reason": "partly"}],
        }
        classes = [item["class"] for item in controller.classify_failures(result)]
        self.assertEqual(classes, ["wrong_result", "forbidden_claim_present", "judge_partial"])

    def test_error_without_details_still_reports(self):
        items = controller.classify_failures({"status": "error", "checks": [], "judge": [], "error": "boom"})
        self.assertEqual([item["class"] for item in items], ["run_error"])

    def test_route_check_is_not_graded(self):
        result = {
            "status": "fail",
            "checks": [
                {"kind": "number_line", "passed": True, "label": "count", "observed": "x"},
                {"kind": "tool_used", "passed": False, "label": "tool", "observed": "z"},
            ],
            "judge": [],
        }
        self.assertEqual(controller.classify_failures(result), [])
        self.assertEqual(controller.effective_status(result), "pass")
        result["checks"][0]["passed"] = False
        self.assertEqual(controller.effective_status(result), "fail")
        self.assertEqual(controller.effective_status({"status": "error", "checks": [], "judge": []}), "error")


class BudgetTests(unittest.TestCase):
    def test_ledger_sums_and_stop_line(self):
        tmp = Path(tempfile.mkdtemp())
        original = controller.RUNS_DIR
        controller.RUNS_DIR = tmp
        try:
            self.assertEqual(controller.ledger_spent(), 0)
            controller.ledger_append({"total_tokens": 100})
            controller.ledger_append({"total_tokens": 250})
            self.assertEqual(controller.ledger_spent(), 350)
            json.loads((tmp / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
        finally:
            controller.RUNS_DIR = original


class StopLineTests(unittest.TestCase):
    def test_zero_means_no_limit(self):
        self.assertFalse(controller.over_stop_line(10_000_000, 400_000, 0))
        self.assertTrue(controller.over_stop_line(5_100_000, 400_000, 5_400_000))
        self.assertFalse(controller.over_stop_line(5_000_000, 400_000, 5_400_000))

    def test_guard_raises_but_never_lowers_the_cap(self):
        spec = controller.build_spec(scenarios.BUILDERS["S01"], set())
        self.assertEqual(controller.build_manifest(spec, "x")["budget_tokens"], spec["budget_tokens"])
        self.assertEqual(controller.build_manifest(spec, "x", 1_500_000)["budget_tokens"], 1_500_000)
        self.assertEqual(controller.build_manifest(spec, "x", 10)["budget_tokens"], spec["budget_tokens"])


class ManifestTests(unittest.TestCase):
    def test_no_public_scenario_grades_the_route(self):
        for sc_id, builder in sorted(scenarios.BUILDERS.items()):
            self.assertNotIn("tool_used", {check["kind"] for check in builder()["checks"]}, sc_id)

    def test_every_public_scenario_is_well_formed(self):
        for sc_id, builder in sorted(scenarios.BUILDERS.items()):
            spec = controller.build_spec(builder, set())
            self.assertEqual(spec["id"], sc_id)
            for key in ("user_turns", "checks", "fixtures", "budget_tokens", "timeout_seconds"):
                self.assertIn(key, spec, sc_id)
            self.assertTrue(spec["user_turns"], sc_id)
            for check in spec["checks"]:
                self.assertIn(check["kind"], KNOWN_CHECK_KINDS, (sc_id, check))
            for judge in spec.get("judge", []):
                self.assertTrue(judge["rubric"], sc_id)
            manifest = controller.build_manifest(spec, "selftest")
            script = controller.compose_script(manifest)
            compile(script, sc_id, "exec")
            self.assertEqual(script.decode("utf-8").count("MANIFEST_B64 = "), 1, sc_id)

    def test_pilot_ids_exist(self):
        for sc_id in scenarios.PILOT:
            self.assertIn(sc_id, scenarios.BUILDERS)

    def test_planted_files_reach_the_manifest_but_not_the_fixtures(self):
        spec = controller.build_spec(scenarios.BUILDERS["S26"], set())
        manifest = controller.build_manifest(spec, "selftest")
        self.assertIn("private_reads.fasta", manifest["scenario"]["planted"])
        self.assertEqual(manifest["scenario"]["fixtures"], {})
        plain = controller.build_manifest(controller.build_spec(scenarios.BUILDERS["S01"], set()), "selftest")
        self.assertEqual(plain["scenario"]["planted"], {})


class HarnessV2Tests(unittest.TestCase):
    def test_no_number_on_terms(self):
        ctx = {"answers": ["平均 Q 值无法给出。"], "runs": [], "files": []}
        self.assertTrue(driver.run_check({"kind": "no_number_on_terms", "terms": ["平均 Q"]}, ctx)["passed"])
        ctx = {"answers": ["平均 Q 为 32.1。"], "runs": [], "files": []}
        self.assertFalse(driver.run_check({"kind": "no_number_on_terms", "terms": ["平均 Q"]}, ctx)["passed"])

    def test_native_run_files_are_attributed_by_time(self):
        import os
        import time

        tmp = Path(tempfile.mkdtemp())
        current = tmp / "run1" / "work"
        current.mkdir(parents=True)
        (current / "aln.sam").write_text("@HD\n")
        stale = tmp / "run0" / "work"
        stale.mkdir(parents=True)
        (stale / "old.sam").write_text("@HD\n")
        since = time.time()
        os.utime(stale / "old.sam", (since - 3600, since - 3600))
        original = driver.NATIVE_RUNS_ROOT
        driver.NATIVE_RUNS_ROOT = tmp
        try:
            rows = driver.list_native_run_files({}, since - 5)
        finally:
            driver.NATIVE_RUNS_ROOT = original
        rels = [row["rel"] for row in rows]
        self.assertIn("native_runs/run1/work/aln.sam", rels)
        self.assertNotIn("native_runs/run0/work/old.sam", rels)

    def test_rejudge_writes_verdict_with_stubbed_llm(self):
        original_call = driver.llm_call
        original_root = driver.OUT_ROOT
        tmp = Path(tempfile.mkdtemp())

        def fake_llm(model, messages, *, max_tokens, temperature):
            usage = {"model": model, "calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            return '{"score": 2, "reason": "ok"}', usage

        driver.llm_call = fake_llm
        driver.OUT_ROOT = tmp
        try:
            verdict = driver.rejudge({
                "mode": "rejudge",
                "tag": "t",
                "round": "r",
                "scenario": {"id": "SX"},
                "history": [{"role": "user", "text": "q"}, {"role": "assistant", "text": "a"}],
                "judge": [{"id": "k", "rubric": "r"}],
                "facts": "f",
            })
        finally:
            driver.llm_call = original_call
            driver.OUT_ROOT = original_root
        self.assertEqual(verdict["judge"][0]["score"], 2)
        self.assertEqual(verdict["usage"]["total_tokens"], 15)
        self.assertTrue((tmp / "r" / "SX" / "rejudge_t.json").exists())

    @staticmethod
    def _tool_event(turn, iteration, tool, params, result):
        step = {
            "kind": "tool",
            "iteration": iteration,
            "action": json.dumps({"tools": [{"tool": tool, "params": params}]}),
            "action_result": result,
        }
        return {"turn": turn, "payload": {"type": "thinking_step", "step": step}}

    def test_evidence_digest_keeps_the_latest_step_and_tool_output(self):
        events = [
            self._tool_event(1, 1, "bio_tools", {"operation": "stats"}, "pending"),
            self._tool_event(1, 1, "bio_tools", {"operation": "stats"}, '[bio_tools] {"ok": true, "output": "reads 150"}'),
            {"turn": 1, "payload": {"type": "reasoning_delta", "text": "ignored"}},
            self._tool_event(1, 2, "execute_code", {"code": "print(1 + 1)"}, '{"output": "2"}'),
        ]
        digest = driver.evidence_digest(events)
        self.assertEqual(digest.count("[turn 1 step 1]"), 1)
        self.assertIn('"output": "reads 150"', digest)
        self.assertNotIn("pending", digest)
        self.assertIn("execute_code(print(1 + 1))", digest)
        self.assertNotIn("ignored", digest)

    def test_evidence_digest_truncates_the_middle(self):
        events = [self._tool_event(1, i, "execute_code", {"code": f"step_{i}"}, '{"output": "x"}') for i in range(1, 40)]
        digest = driver.evidence_digest(events, limit=1200)
        self.assertLessEqual(len(digest), 1300)
        self.assertIn("steps omitted", digest)
        self.assertIn("step 1]", digest)
        self.assertIn("step 39]", digest)

    def test_judge_prompt_carries_the_tool_evidence(self):
        original_call = driver.llm_call
        seen = {}

        def fake_llm(model, messages, *, max_tokens, temperature):
            seen["prompt"] = messages[-1]["content"]
            return '{"score": 2, "reason": "ok"}', {"calls": 1, "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

        driver.llm_call = fake_llm
        try:
            driver.judge_item({"id": "k", "rubric": "r"}, "f", "USER:\nq", "EVIDENCE-LINE")
            self.assertIn("EVIDENCE-LINE", seen["prompt"])
            driver.judge_item({"id": "k", "rubric": "r"}, "f", "USER:\nq")
            self.assertNotIn("Tool evidence", seen["prompt"])
        finally:
            driver.llm_call = original_call


if __name__ == "__main__":
    unittest.main(verbosity=2)
