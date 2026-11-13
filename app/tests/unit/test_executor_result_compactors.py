"""Phase 1: phagescope_data / bio_tools results stay structured for the model.

Before these compactors, oversized results were cut into two half-strings by
``_bound_tool_result_text_for_llm`` (and ``preview.json`` dropped ``stdout``
for having more than five leading keys). The model then burned extra
``file_operations(read)`` calls to recover counts, groups and seqkit output.
The compactors must instead return valid JSON that preserves every field the
model reasons over, with bulky rows projected onto key columns.
"""

from __future__ import annotations

import json

from app.services.deep_think_agent import DeepThinkAgent


def _big_phagescope_result() -> dict:
    return {
        "success": True,
        "tool": "phagescope_data",
        "operation": "search_phages",
        "count": 512,
        "total_exact": True,
        "has_more": True,
        "page": 1,
        "pagesize": 200,
        "returned": 200,
        "results": [
            {
                "id": i,
                "accession": f"ACC_{i}",
                "dataset": "MetaVR",
                "length": 46000 + i,
                "completeness": "Complete",
                "lifestyle": "virulent",
                "host": "Pseudomonas aeruginosa",
                "protein_count": 60,
                "long_free_text_field": "x" * 400,
            }
            for i in range(200)
        ],
    }


def _big_materialize_result() -> dict:
    return {
        "success": True,
        "tool": "phagescope_data",
        "operation": "materialize",
        "materialized_counts": {"fasta": 3, "faa": 3, "gff3": 3},
        "output_directory": "/app/runtime/session_x/results/phagescope/bundle",
        "produced_files": [f"/app/runtime/session_x/results/phagescope/bundle/f{i}.faa" for i in range(60)],
        "truncated_items": [],
        "missing": [{"phage": "X", "kind": "gff3", "reason": "gff3_not_available_locally"}],
        "diagnostics": [{"phage": "X", "dataset": "GPD", "kind": "gff3", "reason": "file_not_found"}],
        "manifest": {"sha256": "y" * 5000, "notes": "z" * 5000},
    }


def _big_bio_tools_result() -> dict:
    return {
        "success": True,
        "tool": "seqkit",
        "operation": "stats",
        "state": "succeeded",
        "exit_code": 0,
        "slurm_job_id": "1643880",
        "command": "seqkit stats genomes.fasta",
        "stdout": "file\tformat\ttype\tnum_seqs\tsum_len\n" + ("genomes.fasta\tFASTA\tDNA\t3\t1,535,998\n" * 200),
        "stderr": "",
        "output_files": ["/app/runtime/session_x/results/seqkit/stats.txt"],
        "padding": "p" * 20000,
    }


class TestPhageScopeDataCompactor:
    def test_search_rows_are_projected_and_counts_survive(self) -> None:
        text = DeepThinkAgent._build_tool_result_text_for_llm(
            tool_name="phagescope_data", result=_big_phagescope_result(), success=True, error=None
        )
        payload = json.loads(text)  # must be valid JSON, not a head/tail string
        result = payload["result"]
        assert result["llm_compacted"] is True
        assert result["count"] == 512
        assert result["has_more"] is True
        assert result["rows_returned"] == 200
        assert result["rows_omitted"] == 185
        assert len(result["results"]) == 15
        row = result["results"][0]
        assert row["accession"] == "ACC_0"
        assert row["dataset"] == "MetaVR"
        assert "long_free_text_field" not in row
        assert "rows_note" in result
        # Bounded size: far below the raw ~90KB payload.
        assert len(text) < 12000

    def test_materialize_paths_counts_and_diagnostics_survive(self) -> None:
        text = DeepThinkAgent._build_tool_result_text_for_llm(
            tool_name="phagescope_data", result=_big_materialize_result(), success=True, error=None
        )
        result = json.loads(text)["result"]
        assert result["materialized_counts"] == {"fasta": 3, "faa": 3, "gff3": 3}
        assert result["output_directory"].endswith("bundle")
        assert len(result["produced_files"]) == 40
        assert result["produced_files_omitted"] == 20
        assert result["missing"][0]["reason"] == "gff3_not_available_locally"
        assert result["diagnostics"][0]["dataset"] == "GPD"
        assert result.get("truncated_items", []) == []

    def test_aggregate_groups_survive(self) -> None:
        result_in = {
            "success": True,
            "operation": "aggregate",
            "group_by": "dataset",
            "groups": [{"key": f"DS{i}", "count": i} for i in range(60)],
            "padding": "g" * 20000,
        }
        text = DeepThinkAgent._build_tool_result_text_for_llm(
            tool_name="phagescope_data", result=result_in, success=True, error=None
        )
        result = json.loads(text)["result"]
        assert result["group_by"] == "dataset"
        assert len(result["groups"]) == 50
        assert result["groups_omitted"] == 10


class TestBioToolsCompactor:
    def test_stdout_slurm_and_command_survive(self) -> None:
        text = DeepThinkAgent._build_tool_result_text_for_llm(
            tool_name="bio_tools", result=_big_bio_tools_result(), success=True, error=None
        )
        result = json.loads(text)["result"]
        assert result["bio_tool"] == "seqkit"
        assert result["operation"] == "stats"
        assert result["slurm_job_id"] == "1643880"
        assert result["exit_code"] == 0
        assert result["command"] == "seqkit stats genomes.fasta"
        assert "num_seqs" in result["stdout"]
        assert "1,535,998" in result["stdout"]
        assert result["stdout_truncated"] is True  # long stdout is head/tail bounded, not dropped
        assert result["output_files"] == ["/app/runtime/session_x/results/seqkit/stats.txt"]
        assert "padding" not in result

    def test_short_stdout_is_not_marked_truncated(self) -> None:
        result_in = _big_bio_tools_result()
        result_in["stdout"] = "file\tformat\ttype\tnum_seqs\tsum_len\ngenomes.fasta\tFASTA\tDNA\t3\t1,535,998\n"
        text = DeepThinkAgent._build_tool_result_text_for_llm(
            tool_name="bio_tools", result=result_in, success=True, error=None
        )
        result = json.loads(text)["result"]
        assert result["stdout"].endswith("1,535,998\n")
        assert "stdout_truncated" not in result
