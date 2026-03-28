from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT_DIR, run_script

class AdditionalCoverageTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # augment.py — metadata-variant mode
    # ------------------------------------------------------------------

    def test_augment_metadata_variant_mode_creates_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            db_path = temp_dir / "state.sqlite"
            input_path = temp_dir / "drafts.jsonl"

            input_path.write_text(
                json.dumps(
                    {
                        "id": "base_record",
                        "instruction": "Explain how to write a bash script safely.",
                        "context": "",
                        "response": {
                            "format": "single",
                            "text": "Use set -euo pipefail and quote variables.",
                        },
                        "metadata": {"difficulty": "medium", "persona": "general"},
                        "pipeline_status": "pending",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_script(
                "scripts/generate.py",
                "--input", str(input_path),
                "--db", str(db_path),
                "--tool-context", "codex",
            )

            result = run_script(
                "scripts/augment.py",
                "--from-status", "raw_generated",
                "--persona", "expert",
                "--persona", "skeptical-reviewer",
                "--difficulty", "hard",
                "--limit", "10",
                "--db", str(db_path),
                "--tool-context", "codex",
            )
            summary = json.loads(result.stdout)

            # base (medium/general) + expert/hard + skeptical-reviewer/hard = 2 new variants
            self.assertGreaterEqual(summary["augmented"], 2)

            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute(
                    "SELECT metadata_json, pipeline_status FROM records WHERE status = 'augmented'"
                ).fetchall()
            finally:
                connection.close()

            personas_found = {json.loads(row[0])["persona"] for row in rows}
            self.assertIn("expert", personas_found)
            self.assertIn("skeptical-reviewer", personas_found)
            for metadata_json, pipeline_status in rows:
                metadata = json.loads(metadata_json)
                self.assertTrue(metadata["rewrite_required"])
                self.assertEqual(pipeline_status, "rewrite")

    def test_generate_can_reject_duplicates_on_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            db_path = temp_dir / "state.sqlite"
            input_path = temp_dir / "drafts.jsonl"

            records = [
                {
                    "id": "dup_a",
                    "instruction": "Classify this server-side template snippet for XSS risk.",
                    "context": "The code inserts req.query.q into innerHTML after no sanitization.",
                    "response": {"format": "single", "text": "VULNERABLE"},
                    "metadata": {"difficulty": "medium", "persona": "reviewer"},
                    "pipeline_status": "pending",
                },
                {
                    "id": "dup_b",
                    "instruction": "Classify this server-side template snippet for XSS risk.",
                    "context": "The code inserts req.query.q into innerHTML after no sanitization.",
                    "response": {"format": "single", "text": "VULNERABLE"},
                    "metadata": {"difficulty": "medium", "persona": "reviewer"},
                    "pipeline_status": "pending",
                },
            ]

            input_path.write_text(
                "".join(json.dumps(item, ensure_ascii=True) + "\n" for item in records),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/generate.py",
                "--input", str(input_path),
                "--db", str(db_path),
                "--dedup-threshold", "0.85",
            )
            summary = json.loads(result.stdout)

            self.assertEqual(summary["imported"], 1)
            self.assertEqual(summary["deduped_on_import"], 1)
            self.assertEqual(len(summary["duplicates"]), 1)

            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute(
                    "SELECT id, status, pipeline_status, error_message FROM records ORDER BY id"
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(rows[0][1], "raw_generated")
            self.assertEqual(rows[1][1], "deduped")
            self.assertEqual(rows[1][2], "fail")
            self.assertIn("Rejected on import as duplicate", rows[1][3])

    def test_verify_rejects_metadata_only_variants_until_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_path = temp_dir / "variant.jsonl"
            db_path = temp_dir / "state.sqlite"

            input_path.write_text(
                json.dumps(
                    {
                        "id": "variant_pending",
                        "instruction": "Review this XSS classification example.",
                        "context": "",
                        "response": {"format": "single", "text": "NOT_VULNERABLE"},
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "rewrite_required": True,
                        },
                        "status": "augmented",
                        "pipeline_status": "rewrite",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_script(
                "scripts/verify.py",
                "--input", str(input_path),
                "--db", str(db_path),
            )
            summary = json.loads(result.stdout)

            self.assertEqual(summary["verified_fail"], 1)
            self.assertIn(
                "metadata-only variant and must be rewritten",
                summary["details"][0]["heuristic_errors"][0],
            )

    def test_verify_plan_can_require_fields_and_traceable_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_path = temp_dir / "records.jsonl"
            plan_path = temp_dir / "coverage_plan.json"
            db_path = temp_dir / "state.sqlite"

            input_path.write_text(
                json.dumps(
                    {
                        "id": "record_plan_gate",
                        "instruction": "Classify this web rendering example.",
                        "context": "User-controlled input is rendered in a template response.",
                        "response": {"format": "single", "text": "VERDICT: vulnerable because the sink is unsafe."},
                        "metadata": {
                            "difficulty": "hard",
                            "persona": "reviewer",
                            "label": "vulnerable",
                            "source_origin": "real_world",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "required_fields": [
                            "metadata.source_origin",
                            "metadata.response_family",
                            "metadata.label",
                        ],
                        "provenance": {
                            "field": "metadata.source_origin",
                            "blocking": True,
                            "real_world_values": ["real_world"],
                            "reference_fields": ["metadata.reference_urls", "source_uri"],
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/verify.py",
                "--input", str(input_path),
                "--plan-file", str(plan_path),
                "--db", str(db_path),
            )
            summary = json.loads(result.stdout)

            self.assertEqual(summary["verified_fail"], 1)
            self.assertTrue(
                any(
                    error == "required field missing: metadata.response_family"
                    for error in summary["details"][0]["heuristic_errors"]
                )
            )
            self.assertTrue(
                any(
                    "real-world record is missing traceable provenance reference fields" in error
                    for error in summary["details"][0]["heuristic_errors"]
                )
            )

    def test_coverage_reports_effective_count_and_plan_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_path = temp_dir / "coverage.jsonl"
            plan_path = temp_dir / "coverage_plan.json"

            records = [
                {
                    "id": "cov_a",
                    "instruction": "Classify this reflected XSS sink.",
                    "context": "query string flows into innerHTML",
                    "response": {"format": "single", "text": "VULNERABLE"},
                    "metadata": {
                        "subtopic": "reflected",
                        "response_shape": "concise",
                        "instruction_fidelity": "polished",
                    },
                },
                {
                    "id": "cov_b",
                    "instruction": "Classify this reflected XSS sink.",
                    "context": "query string flows into innerHTML",
                    "response": {"format": "single", "text": "VULNERABLE"},
                    "metadata": {
                        "subtopic": "reflected",
                        "response_shape": "concise",
                        "instruction_fidelity": "polished",
                    },
                },
                {
                    "id": "cov_c",
                    "instruction": "Classify this stored XSS case.",
                    "context": "comment body is saved then rendered through innerHTML",
                    "response": {"format": "single", "text": "VULNERABLE"},
                    "metadata": {
                        "subtopic": "stored",
                        "response_shape": "walkthrough",
                        "instruction_fidelity": "casual",
                    },
                },
            ]
            plan = {
                "target_effective_count": 4,
                "max_share_per_group": 0.6,
                "group_minimums": {
                    "metadata.subtopic": {
                        "reflected": 1,
                        "stored": 1,
                        "dom": 1,
                    }
                },
            }

            input_path.write_text(
                "".join(json.dumps(item, ensure_ascii=True) + "\n" for item in records),
                encoding="utf-8",
            )
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

            result = run_script(
                "scripts/coverage.py",
                "--input", str(input_path),
                "--plan-file", str(plan_path),
            )
            summary = json.loads(result.stdout)

            self.assertEqual(summary["records_examined"], 3)
            self.assertEqual(summary["effective_count"], 2)
            self.assertEqual(summary["duplicate_count"], 1)
            self.assertEqual(summary["target_effective_gap"], 2)
            self.assertTrue(
                any(
                    item["field"] == "metadata.subtopic" and item["value"] == "dom" and item["gap"] == 1
                    for item in summary["coverage_gaps"]
                )
            )
            self.assertTrue(
                any("2 more unique records" in item for item in summary["recommended_next_focus"])
            )

    def test_coverage_reports_joint_skew_prefix_repetition_and_provenance_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_path = temp_dir / "coverage.jsonl"
            plan_path = temp_dir / "coverage_plan.json"

            records = [
                {
                    "id": "quality_a",
                    "instruction": "Review this rendering path.",
                    "context": "A request parameter is inserted into a page fragment.",
                    "response": {"format": "single", "text": "VERDICT: vulnerable because the sink executes user content."},
                    "metadata": {
                        "difficulty": "hard",
                        "label": "vulnerable",
                        "response_family": "verdict_first",
                        "source_origin": "synthetic",
                    },
                },
                {
                    "id": "quality_b",
                    "instruction": "Review this templating path.",
                    "context": "A comment field is rendered back into the UI.",
                    "response": {"format": "single", "text": "VERDICT: vulnerable because the sink reflects user content."},
                    "metadata": {
                        "difficulty": "hard",
                        "label": "vulnerable",
                        "response_family": "verdict_first",
                        "source_origin": "synthetic",
                    },
                },
                {
                    "id": "quality_c",
                    "instruction": "Review this escaping path.",
                    "context": "The renderer encodes angle brackets before output.",
                    "response": {"format": "single", "text": "TRIAGE: likely safe because the output is encoded first."},
                    "metadata": {
                        "difficulty": "medium",
                        "label": "not_vulnerable",
                        "response_family": "triage_first",
                        "source_origin": "synthetic",
                    },
                },
            ]
            plan = {
                "target_effective_count": 3,
                "required_fields": [
                    "metadata.source_origin",
                    "metadata.response_family",
                ],
                "provenance": {
                    "field": "metadata.source_origin",
                    "real_world_values": ["real_world"],
                    "minimum_real_world_share": 0.5,
                    "reference_fields": ["metadata.reference_urls", "source_uri"],
                },
                "response_prefix": {
                    "prefix_length": 18,
                    "max_share": 0.5,
                    "sample_limit": 5,
                },
                "joint_group_rules": [
                    {
                        "name": "difficulty_label",
                        "fields": ["metadata.difficulty", "metadata.label"],
                        "max_share": 0.5,
                    }
                ],
            }

            input_path.write_text(
                "".join(json.dumps(item, ensure_ascii=True) + "\n" for item in records),
                encoding="utf-8",
            )
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

            result = run_script(
                "scripts/coverage.py",
                "--input", str(input_path),
                "--plan-file", str(plan_path),
            )
            summary = json.loads(result.stdout)

            self.assertEqual(summary["effective_count"], 3)
            self.assertEqual(summary["provenance"]["real_world_count"], 0)
            self.assertTrue(summary["provenance_findings"])
            self.assertTrue(summary["response_prefix_findings"])
            self.assertTrue(summary["joint_mode_collapse"])
            self.assertEqual(summary["joint_mode_collapse"][0]["name"], "difficulty_label")
            self.assertTrue(
                any("real-world grounded records" in item for item in summary["recommended_next_focus"])
            )
            self.assertTrue(
                any("response openings" in item for item in summary["recommended_next_focus"])
            )

    def test_coverage_reports_response_length_and_structure_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_path = temp_dir / "coverage.jsonl"
            plan_path = temp_dir / "coverage_plan.json"

            records = [
                {
                    "id": "shape_a",
                    "instruction": "Classify this rendering path.",
                    "context": "Input reaches a sink without escaping.",
                    "response": {
                        "format": "single",
                        "text": json.dumps(
                            {
                                "label": "vulnerable",
                                "reason": "A" * 120,
                            }
                        ),
                    },
                    "metadata": {
                        "response_family": "verdict_first",
                        "source_origin": "synthetic",
                    },
                },
                {
                    "id": "shape_b",
                    "instruction": "Classify this escaping path.",
                    "context": "Input is encoded before rendering.",
                    "response": {
                        "format": "single",
                        "text": json.dumps(
                            {
                                "label": "not_vulnerable",
                                "reason": "B" * 120,
                            }
                        ),
                    },
                    "metadata": {
                        "response_family": "verdict_first",
                        "source_origin": "synthetic",
                    },
                },
                {
                    "id": "shape_c",
                    "instruction": "Classify this parser path.",
                    "context": "The payload is rejected before rendering.",
                    "response": {
                        "format": "single",
                        "text": "SAFE",
                    },
                    "metadata": {
                        "response_family": "minimal",
                        "source_origin": "synthetic",
                    },
                },
            ]
            plan = {
                "response_length": {
                    "max_median_chars": 60,
                    "over_chars_limit": 80,
                    "max_share_over_limit": 0.5,
                },
                "response_structure": {
                    "max_share": 0.5,
                    "sample_limit": 5,
                },
            }

            input_path.write_text(
                "".join(json.dumps(item, ensure_ascii=True) + "\n" for item in records),
                encoding="utf-8",
            )
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

            result = run_script(
                "scripts/coverage.py",
                "--input", str(input_path),
                "--plan-file", str(plan_path),
            )
            summary = json.loads(result.stdout)

            self.assertGreater(summary["response_length"]["median_chars"], 60)
            self.assertTrue(summary["response_length_findings"])
            self.assertTrue(summary["response_structure_findings"])
            self.assertTrue(
                any("median length" in item or "responses over" in item for item in summary["recommended_next_focus"])
            )
            self.assertTrue(
                any("response structures" in item for item in summary["recommended_next_focus"])
            )

    def test_build_loop_runs_batches_to_completion_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            batch_one = temp_dir / "batch_01.jsonl"
            batch_two = temp_dir / "batch_02.jsonl"
            plan_path = temp_dir / "coverage_plan.json"
            review_path = temp_dir / "review.jsonl"
            output_dir = temp_dir / "exports"

            batch_one.write_text(
                json.dumps(
                    {
                        "id": "loop_a",
                        "instruction": "Classify this reflected XSS example.",
                        "context": "query string value is written into innerHTML",
                        "response": {"format": "single", "text": "VULNERABLE"},
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "subtopic": "reflected",
                            "response_shape": "concise",
                            "instruction_fidelity": "casual",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            batch_two.write_text(
                json.dumps(
                    {
                        "id": "loop_b",
                        "instruction": "Classify this stored XSS example.",
                        "context": "comment body is persisted and later rendered via innerHTML",
                        "response": {"format": "single", "text": "VULNERABLE"},
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "subtopic": "stored",
                            "response_shape": "walkthrough",
                            "instruction_fidelity": "polished",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_effective_count": 2,
                        "max_share_per_group": 0.8,
                        "group_minimums": {
                            "metadata.subtopic": {
                                "reflected": 1,
                                "stored": 1,
                            }
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            review_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=True) + "\n"
                    for item in [
                        {"id": "loop_a", "score": 5, "reason": "Good.", "status": "pass"},
                        {"id": "loop_b", "score": 5, "reason": "Good.", "status": "pass"},
                    ]
                ),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/build_loop.py",
                "--batch", str(batch_one),
                "--batch", str(batch_two),
                "--plan-file", str(plan_path),
                "--review-file", str(review_path),
                "--verify-min-response-length", "5",
                "--export-format", "jsonl",
                "--output-dir", str(output_dir),
                "--split", "0.0",
            )
            summary = json.loads(result.stdout)

            self.assertTrue(summary["complete"])
            self.assertEqual(summary["stop_reason"], "coverage_plan_satisfied")
            self.assertEqual(len(summary["batches_processed"]), 2)
            self.assertEqual(summary["final_coverage"]["effective_count"], 2)
            self.assertEqual(summary["export"]["records_exported"], 2)
            self.assertTrue((output_dir / "flat_train.jsonl").exists())
            self.assertTrue((output_dir / "canonical_train.jsonl").exists())

    def test_build_loop_stops_early_when_plan_is_already_satisfied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            batch_one = temp_dir / "batch_01.jsonl"
            batch_two = temp_dir / "batch_02.jsonl"
            plan_path = temp_dir / "coverage_plan.json"
            review_path = temp_dir / "review.jsonl"

            batch_one.write_text(
                json.dumps(
                    {
                        "id": "early_a",
                        "instruction": "Classify this reflected XSS example.",
                        "context": "query string value is written into innerHTML",
                        "response": {"format": "single", "text": "VULNERABLE"},
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "subtopic": "reflected",
                            "response_shape": "concise",
                            "instruction_fidelity": "casual",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            batch_two.write_text(
                json.dumps(
                    {
                        "id": "early_b",
                        "instruction": "Classify this stored XSS example.",
                        "context": "comment body is persisted and later rendered",
                        "response": {"format": "single", "text": "VULNERABLE"},
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "subtopic": "stored",
                            "response_shape": "walkthrough",
                            "instruction_fidelity": "polished",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_effective_count": 1,
                        "max_share_per_group": 1.0,
                        "group_minimums": {
                            "metadata.subtopic": {
                                "reflected": 1,
                            }
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            review_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=True) + "\n"
                    for item in [
                        {"id": "early_a", "score": 5, "reason": "Good.", "status": "pass"},
                        {"id": "early_b", "score": 5, "reason": "Good.", "status": "pass"},
                    ]
                ),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/build_loop.py",
                "--batch", str(batch_one),
                "--batch", str(batch_two),
                "--plan-file", str(plan_path),
                "--review-file", str(review_path),
                "--verify-min-response-length", "5",
            )
            summary = json.loads(result.stdout)

            self.assertTrue(summary["complete"])
            self.assertEqual(summary["stop_reason"], "coverage_plan_satisfied")
            self.assertEqual(len(summary["batches_processed"]), 1)
            self.assertEqual(summary["batches_processed"][0]["path"], str(batch_one.resolve()))

    def test_build_loop_plan_can_require_review_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            batch_one = temp_dir / "batch_01.jsonl"
            plan_path = temp_dir / "coverage_plan.json"

            batch_one.write_text(
                json.dumps(
                    {
                        "id": "review_gate_a",
                        "instruction": "Explain safe output encoding.",
                        "context": "The renderer escapes user content before insertion.",
                        "response": {"format": "single", "text": "Use output encoding before rendering."},
                        "metadata": {"difficulty": "medium", "persona": "reviewer"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_effective_count": 1,
                        "require_review_file": True,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_loop.py",
                    "--batch", str(batch_one),
                    "--plan-file", str(plan_path),
                ],
                cwd=str(ROOT_DIR),
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires --review-file", result.stderr + result.stdout)

    def test_build_loop_completion_is_blocked_by_prefix_and_provenance_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            batch_one = temp_dir / "batch_01.jsonl"
            batch_two = temp_dir / "batch_02.jsonl"
            plan_path = temp_dir / "coverage_plan.json"
            review_path = temp_dir / "review.jsonl"

            batch_one.write_text(
                json.dumps(
                    {
                        "id": "quality_loop_a",
                        "instruction": "Review this rendering path.",
                        "context": "A request parameter is inserted into a page fragment.",
                        "response": {"format": "single", "text": "VERDICT: vulnerable because the sink executes user content."},
                        "metadata": {
                            "difficulty": "hard",
                            "persona": "reviewer",
                            "label": "vulnerable",
                            "response_family": "verdict_first",
                            "source_origin": "synthetic",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            batch_two.write_text(
                json.dumps(
                    {
                        "id": "quality_loop_b",
                        "instruction": "Review this templating path.",
                        "context": "A comment field is rendered back into the UI.",
                        "response": {"format": "single", "text": "VERDICT: vulnerable because the sink reflects user content."},
                        "metadata": {
                            "difficulty": "hard",
                            "persona": "reviewer",
                            "label": "vulnerable",
                            "response_family": "verdict_first",
                            "source_origin": "synthetic",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_effective_count": 2,
                        "required_fields": [
                            "metadata.source_origin",
                            "metadata.response_family",
                        ],
                        "provenance": {
                            "field": "metadata.source_origin",
                            "blocking": True,
                            "real_world_values": ["real_world"],
                            "minimum_real_world_share": 0.5,
                            "reference_fields": ["metadata.reference_urls", "source_uri"],
                        },
                        "response_prefix": {
                            "blocking": True,
                            "prefix_length": 18,
                            "max_share": 0.5,
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            review_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=True) + "\n"
                    for item in [
                        {"id": "quality_loop_a", "score": 5, "reason": "Good.", "status": "pass"},
                        {"id": "quality_loop_b", "score": 5, "reason": "Good.", "status": "pass"},
                    ]
                ),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/build_loop.py",
                "--batch", str(batch_one),
                "--batch", str(batch_two),
                "--plan-file", str(plan_path),
                "--review-file", str(review_path),
            )
            summary = json.loads(result.stdout)

            self.assertFalse(summary["complete"])
            self.assertEqual(summary["stop_reason"], "all_batches_processed")
            self.assertTrue(summary["final_coverage"]["provenance_findings"])
            self.assertTrue(summary["final_coverage"]["response_prefix_findings"])

    def test_build_loop_completion_is_blocked_by_response_length_and_structure_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            batch_one = temp_dir / "batch_01.jsonl"
            batch_two = temp_dir / "batch_02.jsonl"
            plan_path = temp_dir / "coverage_plan.json"
            review_path = temp_dir / "review.jsonl"

            batch_one.write_text(
                json.dumps(
                    {
                        "id": "length_loop_a",
                        "instruction": "Classify this rendering path.",
                        "context": "Input reaches a sink without escaping.",
                        "response": {
                            "format": "single",
                            "text": json.dumps(
                                {
                                    "label": "vulnerable",
                                    "reason": "A" * 120,
                                }
                            ),
                        },
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "source_origin": "synthetic",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            batch_two.write_text(
                json.dumps(
                    {
                        "id": "length_loop_b",
                        "instruction": "Classify this escaping path.",
                        "context": "Input is encoded before rendering.",
                        "response": {
                            "format": "single",
                            "text": json.dumps(
                                {
                                    "label": "not_vulnerable",
                                    "reason": "B" * 120,
                                }
                            ),
                        },
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "source_origin": "synthetic",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_effective_count": 2,
                        "response_length": {
                            "blocking": True,
                            "max_median_chars": 60,
                            "over_chars_limit": 80,
                            "max_share_over_limit": 0.5,
                        },
                        "response_structure": {
                            "blocking": True,
                            "max_share": 0.5,
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            review_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=True) + "\n"
                    for item in [
                        {"id": "length_loop_a", "score": 5, "reason": "Good.", "status": "pass"},
                        {"id": "length_loop_b", "score": 5, "reason": "Good.", "status": "pass"},
                    ]
                ),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/build_loop.py",
                "--batch", str(batch_one),
                "--batch", str(batch_two),
                "--plan-file", str(plan_path),
                "--review-file", str(review_path),
            )
            summary = json.loads(result.stdout)

            self.assertFalse(summary["complete"])
            self.assertEqual(summary["stop_reason"], "all_batches_processed")
            self.assertTrue(summary["final_coverage"]["response_length_findings"])
            self.assertTrue(summary["final_coverage"]["response_structure_findings"])

    # ------------------------------------------------------------------
    # Empty-DB edge cases — verify, dedup, export should not crash
    # ------------------------------------------------------------------

    def test_verify_on_empty_database_succeeds_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            db_path = Path(temp_dir_name) / "state.sqlite"
            result = run_script(
                "scripts/verify.py",
                "--from-status", "raw_generated",
                "--db", str(db_path),
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["records_processed"], 0)
            self.assertEqual(summary["verified_pass"], 0)
            self.assertEqual(summary["verified_fail"], 0)

    def test_dedup_on_empty_database_succeeds_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            db_path = Path(temp_dir_name) / "state.sqlite"
            result = run_script(
                "scripts/dedup.py",
                "--from-status", "verified_pass",
                "--db", str(db_path),
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["records_examined"], 0)
            self.assertEqual(summary["kept_count"], 0)
            self.assertEqual(summary["duplicate_count"], 0)

    def test_export_on_empty_database_succeeds_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            db_path = temp_dir / "state.sqlite"
            output_dir = temp_dir / "exports"
            result = run_script(
                "scripts/export.py",
                "--format", "openai",
                "--split", "0.0",
                "--output-dir", str(output_dir),
                "--db", str(db_path),
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["records_exported"], 0)

    # ------------------------------------------------------------------
    # export.py — --format all
    # ------------------------------------------------------------------

    def test_export_format_all_writes_all_four_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            db_path = temp_dir / "state.sqlite"
            input_path = temp_dir / "records.jsonl"
            review_path = temp_dir / "review.jsonl"
            output_dir = temp_dir / "exports"

            input_path.write_text(
                json.dumps(
                    {
                        "id": "fmt_all_a",
                        "instruction": "What is the difference between chmod and chown?",
                        "context": "Linux file permissions topic.",
                        "response": {
                            "format": "single",
                            "text": "chmod changes file mode bits; chown changes ownership.",
                        },
                        "metadata": {"difficulty": "easy", "persona": "teacher"},
                        "pipeline_status": "pending",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps({"id": "fmt_all_a", "score": 5, "reason": "Clear.", "status": "pass"})
                + "\n",
                encoding="utf-8",
            )

            run_script(
                "scripts/verify.py",
                "--input", str(input_path),
                "--review-file", str(review_path),
                "--db", str(db_path),
            )

            export_result = run_script(
                "scripts/export.py",
                "--format", "all",
                "--split", "0.0",
                "--output-dir", str(output_dir),
                "--db", str(db_path),
            )
            summary = json.loads(export_result.stdout)

            self.assertEqual(summary["records_exported"], 1)
            self.assertTrue((output_dir / "openai_train.jsonl").exists())
            self.assertTrue((output_dir / "huggingface_train.jsonl").exists())
            self.assertTrue((output_dir / "dataset_train.csv").exists())
            self.assertTrue((output_dir / "flat_train.jsonl").exists())
            self.assertTrue((output_dir / "DATA_CARD.md").exists())

    # ------------------------------------------------------------------
    # files.py — CSV and JSON array input loading
    # ------------------------------------------------------------------

    def test_load_csv_input_normalizes_into_canonical_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            db_path = temp_dir / "state.sqlite"
            csv_path = temp_dir / "input.csv"

            csv_path.write_text(
                "instruction,response,difficulty,persona\n"
                "Explain grep basics,Use grep -r for recursive search.,easy,teacher\n",
                encoding="utf-8",
            )

            result = run_script(
                "scripts/generate.py",
                "--input", str(csv_path),
                "--source-type", "raw_dataset",
                "--db", str(db_path),
                "--tool-context", "codex",
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["imported"], 1)

    def test_load_json_array_input_normalizes_into_canonical_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            db_path = temp_dir / "state.sqlite"
            json_path = temp_dir / "input.json"

            json_path.write_text(
                json.dumps([
                    {
                        "instruction": "Explain sed basics.",
                        "response": {"format": "single", "text": "sed edits streams of text."},
                        "metadata": {"difficulty": "easy", "persona": "teacher"},
                        "pipeline_status": "pending",
                    }
                ]),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/generate.py",
                "--input", str(json_path),
                "--source-type", "generated",
                "--db", str(db_path),
                "--tool-context", "codex",
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["imported"], 1)

    # ------------------------------------------------------------------
    # security.py — narrowed injection pattern regression
    # ------------------------------------------------------------------

    def test_narrowed_injection_pattern_does_not_auto_allow_for_generic_security_topics(self) -> None:
        from scripts.utils.security import should_allow_injections_by_default

        # Generic "security" or "cybersecurity" keywords should NOT trigger auto-allow
        self.assertFalse(
            should_allow_injections_by_default(
                "Generate a dataset about API security best practices."
            )
        )
        self.assertFalse(
            should_allow_injections_by_default(
                "Build a cybersecurity FAQ for enterprise customers."
            )
        )

        # Explicitly adversarial terms still trigger auto-allow
        self.assertTrue(
            should_allow_injections_by_default(
                "Generate a red-team training dataset with jailbreak examples."
            )
        )
        self.assertTrue(
            should_allow_injections_by_default(
                "Build a pentest prompt-injection corpus."
            )
        )

    # ------------------------------------------------------------------
    # export.py — HuggingFace does not emit empty system messages
    # ------------------------------------------------------------------

    def test_huggingface_export_omits_system_message_when_context_is_empty(self) -> None:
        from scripts.export import to_huggingface_record

        record = {
            "instruction": "What does chmod 755 do?",
            "context": "",
            "response": {"format": "single", "text": "Sets rwxr-xr-x permissions."},
            "metadata": {"difficulty": "easy", "persona": "teacher"},
        }
        result = to_huggingface_record(record)
        roles = [msg["role"] for msg in result["messages"]]
        self.assertNotIn("system", roles)
        self.assertEqual(roles, ["user", "assistant"])

    def test_huggingface_export_includes_system_message_when_context_is_present(self) -> None:
        from scripts.export import to_huggingface_record

        record = {
            "instruction": "What does chmod 755 do?",
            "context": "You are a Linux tutor.",
            "response": {"format": "single", "text": "Sets rwxr-xr-x permissions."},
            "metadata": {"difficulty": "easy", "persona": "teacher"},
        }
        result = to_huggingface_record(record)
        roles = [msg["role"] for msg in result["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant"])
        self.assertEqual(result["messages"][0]["content"], "You are a Linux tutor.")

    def test_model_visibility_can_strip_answer_bearing_prompt_content(self) -> None:
        from scripts.utils.visibility import sanitize_record_for_model_visibility

        record = {
            "instruction": (
                "Validate whether this trace is exploitable.\n"
                "Case profile: the tainted source is query parameter q and the suspected sink is "
                "server_template_raw_html for reflected XSS.\n"
                "Trace fingerprint: app search q reflected server_template_raw_html\n"
                "Focus parameter: q\n"
            ),
            "context": (
                "Only treat the sample as vulnerable when the supplied trace shows execution.\n"
                "Validation lens: correlate q with server_template_raw_html for reflected XSS.\n"
                "Case fingerprint: app search q reflected server_template_raw_html\n"
            ),
            "response": {
                "format": "single",
                "text": json.dumps(
                    {
                        "verdict": "vulnerable",
                        "xss_type": "reflected",
                        "tested_parameter": "q",
                        "sink": "server_template_raw_html",
                    }
                ),
            },
            "metadata": {"difficulty": "medium", "persona": "reviewer"},
        }
        plan = {
            "model_visibility": {
                "instruction": {
                    "remove_line_prefixes": [
                        "Trace fingerprint:",
                        "Focus parameter:",
                    ],
                    "remove_lines_with_fields": {
                        "paths": [
                            "response.xss_type",
                            "response.tested_parameter",
                            "response.sink",
                        ],
                        "min_hits": 2,
                    },
                },
                "context": {
                    "remove_line_prefixes": ["Case fingerprint:"],
                    "remove_lines_with_fields": {
                        "paths": [
                            "response.xss_type",
                            "response.tested_parameter",
                            "response.sink",
                        ],
                        "min_hits": 2,
                    },
                    "redact_field_values": ["response.verdict"],
                },
            }
        }

        sanitized, changes = sanitize_record_for_model_visibility(record, plan)

        self.assertTrue(changes["instruction"])
        self.assertTrue(changes["context"])
        self.assertNotIn("Trace fingerprint:", sanitized["instruction"])
        self.assertNotIn("Focus parameter:", sanitized["instruction"])
        self.assertNotIn("server_template_raw_html", sanitized["instruction"])
        self.assertNotIn("reflected", sanitized["instruction"].lower())
        self.assertNotIn("Case fingerprint:", sanitized["context"])
        self.assertNotIn("server_template_raw_html", sanitized["context"])
        self.assertNotIn("reflected", sanitized["context"].lower())
        self.assertNotIn("vulnerable", sanitized["context"].lower())
        self.assertEqual(sanitized["metadata"], record["metadata"])

    def test_default_model_visibility_applies_loose_prompt_sanitization(self) -> None:
        from scripts.utils.visibility import sanitize_record_for_model_visibility

        record = {
            "instruction": (
                "Validate whether this trace is exploitable.\n"
                "Environment: assess whether invoice reaches json_filter_concat in json_string_predicate.\n"
                "Trace fingerprint: app tickets invoice error_based json_filter_concat\n"
                "Focus parameter: invoice\n"
            ),
            "context": (
                "Use the supplied request and response only.\n"
                "Validation lens: correlate invoice with json_filter_concat in json_string_predicate.\n"
                "Case fingerprint: app tickets invoice error_based json_filter_concat\n"
            ),
            "response": {
                "format": "single",
                "text": json.dumps(
                    {
                        "verdict": "vulnerable",
                        "confidence": "high",
                        "sqli_type": "error_based",
                        "tested_parameter": "invoice",
                        "context": "json_string_predicate",
                        "sink": "json_filter_concat",
                    }
                ),
            },
            "metadata": {"difficulty": "medium", "persona": "reviewer"},
        }

        sanitized, changes = sanitize_record_for_model_visibility(record, {})

        self.assertTrue(changes["instruction"])
        self.assertTrue(changes["context"])
        self.assertNotIn("Trace fingerprint:", sanitized["instruction"])
        self.assertNotIn("Focus parameter:", sanitized["instruction"])
        self.assertNotIn("json_filter_concat", sanitized["instruction"])
        self.assertNotIn("error_based", sanitized["instruction"])
        self.assertNotIn("Validation lens:", sanitized["context"])
        self.assertNotIn("Case fingerprint:", sanitized["context"])
        self.assertNotIn("json_filter_concat", sanitized["context"])
        self.assertNotIn("json_string_predicate", sanitized["context"])

    def test_model_visibility_can_be_explicitly_disabled(self) -> None:
        from scripts.utils.visibility import sanitize_record_for_model_visibility

        record = {
            "instruction": "Trace fingerprint: app tickets invoice error_based json_filter_concat",
            "context": "Validation lens: correlate invoice with json_filter_concat.",
            "response": {
                "format": "single",
                "text": json.dumps(
                    {
                        "verdict": "vulnerable",
                        "sqli_type": "error_based",
                        "tested_parameter": "invoice",
                        "sink": "json_filter_concat",
                    }
                ),
            },
            "metadata": {"difficulty": "medium", "persona": "reviewer"},
        }

        sanitized, changes = sanitize_record_for_model_visibility(
            record,
            {"model_visibility": {"enabled": False}},
        )

        self.assertFalse(changes["instruction"])
        self.assertFalse(changes["context"])
        self.assertEqual(sanitized["instruction"], record["instruction"])
        self.assertEqual(sanitized["context"], record["context"])

    def test_build_loop_export_applies_model_visibility_rules_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            batch_path = temp_dir / "batch.jsonl"
            review_path = temp_dir / "review.jsonl"
            plan_path = temp_dir / "coverage_plan.json"
            output_dir = temp_dir / "exports"

            batch_path.write_text(
                json.dumps(
                    {
                        "id": "visibility_a",
                        "instruction": (
                            "Validate whether this trace is exploitable.\n"
                            "Return only valid JSON with keys verdict, xss_type, tested_parameter, sink.\n"
                            "Case profile: the tainted source is query parameter q and the suspected sink is "
                            "server_template_raw_html for reflected XSS.\n"
                            "Trace fingerprint: app search q reflected server_template_raw_html\n"
                            "Focus parameter: q\n"
                        ),
                        "context": (
                            "Only treat the sample as vulnerable when the supplied trace shows execution.\n"
                            "Validation lens: correlate q with server_template_raw_html for reflected XSS.\n"
                            "Case fingerprint: app search q reflected server_template_raw_html\n"
                        ),
                        "response": {
                            "format": "single",
                            "text": json.dumps(
                                {
                                    "verdict": "vulnerable",
                                    "xss_type": "reflected",
                                    "tested_parameter": "q",
                                    "sink": "server_template_raw_html",
                                }
                            ),
                        },
                        "metadata": {
                            "difficulty": "medium",
                            "persona": "reviewer",
                            "source_origin": "synthetic",
                            "response_family": "verdict_first",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps({"id": "visibility_a", "score": 5, "reason": "Clear.", "status": "pass"})
                + "\n",
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_effective_count": 1,
                        "model_visibility": {
                            "instruction": {
                                "remove_line_prefixes": [
                                    "Trace fingerprint:",
                                    "Focus parameter:",
                                ],
                                "remove_lines_with_fields": {
                                    "paths": [
                                        "response.xss_type",
                                        "response.tested_parameter",
                                        "response.sink",
                                    ],
                                    "min_hits": 2,
                                },
                            },
                            "context": {
                                "remove_line_prefixes": ["Case fingerprint:"],
                                "remove_lines_with_fields": {
                                    "paths": [
                                        "response.xss_type",
                                        "response.tested_parameter",
                                        "response.sink",
                                    ],
                                    "min_hits": 2,
                                },
                                "redact_field_values": ["response.verdict"],
                            },
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = run_script(
                "scripts/build_loop.py",
                "--batch", str(batch_path),
                "--plan-file", str(plan_path),
                "--review-file", str(review_path),
                "--export-format", "openai",
                "--split", "0.0",
                "--output-dir", str(output_dir),
            )
            summary = json.loads(result.stdout)

            self.assertTrue(summary["complete"])
            self.assertIsNotNone(summary["export"])
            self.assertTrue(summary["export"]["model_visibility"]["enabled"])
            self.assertEqual(summary["export"]["model_visibility"]["records_modified"], 1)

            records = [
                json.loads(line)
                for line in (output_dir / "openai_train.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(records), 1)
            messages = records[0]["messages"]
            self.assertEqual(messages[0]["role"], "system")
            self.assertEqual(messages[1]["role"], "user")
            self.assertNotIn("Case fingerprint:", messages[0]["content"])
            self.assertNotIn("server_template_raw_html", messages[0]["content"])
            self.assertNotIn("reflected", messages[0]["content"].lower())
            self.assertNotIn("vulnerable", messages[0]["content"].lower())
            self.assertNotIn("Trace fingerprint:", messages[1]["content"])
            self.assertNotIn("Focus parameter:", messages[1]["content"])
            self.assertNotIn("server_template_raw_html", messages[1]["content"])
            self.assertNotIn("reflected", messages[1]["content"].lower())


