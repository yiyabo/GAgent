"""Pipeline engine: dependency order, parallelism, failures, cache, cancellation and the report node.

bio_tools, the NCBI download and the model are replaced by fakes that write what the real tools write.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.database import init_db
from app.database_pool import get_db
from app.repository import pipelines as repo
from app.services.pipelines import engine, report, service
from app.services.session_paths import get_runtime_session_dir

FASTA_NT = ">seq1 test genome\nATGCGCGCATATGCGC\n>seq2\nGGGCCCAAATTT\n"
FASTA_NT2 = ">t7 another genome\nTTTTGGGGCCCCAAAA\n"
FASTA_AA = ">p1\nMKKLLV\n>p2\nMSTNPKPQRKTKRNTNRRPQDVKFPGG\n"


class FakeBioTools:
    """Writes the files each operation would write into its own run directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: List[Dict[str, Any]] = []
        self.active = 0
        self.peak = 0
        self.delay = 0.0
        self.fail: Dict[str, str] = {}
        self.skip_files: set = set()
        self.block: asyncio.Event | None = None

    async def __call__(self, tool_name, operation, input_file=None, params=None, timeout=None, session_id=None, tool_context=None, **_):
        params = dict(params or {})
        self.calls.append({"tool": tool_name, "operation": operation, "input_file": input_file, "params": params, "timeout": timeout})
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.block is not None and tool_name == "genomad":
                await self.block.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            key = f"{tool_name}.{operation}"
            if key in self.fail:
                return {"success": False, "error": self.fail[key], "error_code": "execution_failed", "stderr": "boom\n" * 3}
            work = self.root / "bio_tools" / "_native" / "runs" / uuid.uuid4().hex[:12] / "work"
            work.mkdir(parents=True)
            self._write(work, tool_name, operation, params)
            return {
                "success": True,
                "tool": tool_name,
                "operation": operation,
                "command": f"{tool_name} {operation} {input_file or ''} -out {work}/result -evalue 10",
                "work_dir": str(work),
                "stdout": "file\tnum_seqs\nx.fa\t2\n" if tool_name == "seqkit" else "",
                "execution_mode": "native",
                "slurm_job_id": "4242",
                "exit_code": 0,
            }
        finally:
            self.active -= 1

    def _write(self, work: Path, tool: str, operation: str, params: Dict[str, Any]) -> None:
        def put(rel: str, text: str) -> None:
            if rel in self.skip_files:
                return
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

        if tool == "prodigal":
            put(params["protein"], FASTA_AA)
            put(params["nucleotide"], FASTA_NT)
            put(params["gff"], "##gff-version 3\n")
        elif tool == "blast" and operation == "makeblastdb":
            for ext in ("pin", "psq", "phr"):
                put(f"{params['db']}.{ext}", "db")
        elif tool in {"blast", "mmseqs2"}:
            put(params["output"], "q1\tt1\t99.0\t50\t0\t0\t1\t50\t1\t50\t1e-20\t95.1\n")
        elif tool in {"fastani", "hmmer"}:
            put(params["output"], "q1\tt1\t99.0\n")
        elif tool == "checkv":
            put(f"{params['output']}/quality_summary.tsv", "contig_id\tcheckv_quality\nseq1\tComplete\n")
        elif tool == "genomad":
            put(f"{params['output']}/x_summary/x_virus_summary.tsv", "seq_name\tvirus_score\nseq1\t0.98\n")
        elif tool == "phabox2":
            put(f"{params['output']}/final_prediction/{operation}_prediction.tsv", "Accession\tPred\nseq1\tvirulent\n")
        elif tool == "virsorter2":
            put(f"{params['output']}/final-viral-score.tsv", "seqname\tmax_score\nseq1\t0.97\n")
        elif tool == "vibrant":
            put(f"{params['output']}/VIBRANT_summary.tsv", "phages\t1\n")
        elif tool == "bakta":
            put(f"{params['output']}/{params['prefix']}.faa", FASTA_AA)
            put(f"{params['output']}/{params['prefix']}.gff3", "##gff-version 3\n")


@pytest.fixture
def env(isolated_app_env, monkeypatch):
    import tool_box.bio_tools as bio_tools_pkg

    init_db()
    fake = FakeBioTools(isolated_app_env["runtime_root"])
    monkeypatch.setattr(bio_tools_pkg, "bio_tools_handler", fake)
    monkeypatch.setattr(service, "launch", lambda run_id: None)
    monkeypatch.setattr(engine, "POLL_SECONDS", 0.05)

    async def fake_model(prompt, session_id, run_id):
        fake.last_prompt = prompt
        return "# Report\n\nAll good."

    monkeypatch.setattr(report, "_call_model", fake_model)
    return fake


def make_pipeline(graph: Dict[str, Any], files: Dict[str, str]) -> str:
    created = service.create("owner-1", "test", "", {"schema": 1, "nodes": {}})
    pipeline_id = created["id"]
    uploads = service.session_dir(pipeline_id) / "uploads"
    for name, text in files.items():
        (uploads / name).write_text(text)
    for node in graph["nodes"].values():
        path = (node.get("params") or {}).get("path")
        if path:
            node["params"]["path"] = str(uploads / path)
    service.update(pipeline_id, graph=graph)
    return pipeline_id


def two_genome_graph() -> Dict[str, Any]:
    return {
        "schema": 1,
        "nodes": {
            "g1": {"type": "input.genome", "params": {"path": "lambda.fasta"}},
            "g2": {"type": "input.genome", "params": {"path": "t7.fasta"}},
            "stats": {"type": "seqkit.stats", "inputs": {"sequences": {"node": "g1", "port": "file"}}},
            "p1": {"type": "prodigal.predict", "inputs": {"genome": {"node": "g1", "port": "file"}}},
            "p2": {"type": "prodigal.predict", "inputs": {"genome": {"node": "g2", "port": "file"}}},
            "db": {"type": "blast.makeblastdb_aa", "inputs": {"proteins": {"node": "p1", "port": "proteins"}}},
            "bp": {"type": "blast.blastp", "inputs": {"query": {"node": "p2", "port": "proteins"}, "db": {"node": "db", "port": "db"}}},
            "mm": {
                "type": "mmseqs2.protein_search",
                "inputs": {"query": {"node": "p2", "port": "proteins"}, "target": {"node": "p1", "port": "proteins"}},
            },
            "cv": {"type": "checkv.end_to_end", "inputs": {"sequences": {"node": "g1", "port": "file"}}},
            "rep": {
                "type": "agent.report",
                "params": {"language": "en"},
                "inputs": {
                    "evidence": [
                        {"node": "stats", "port": "stats"},
                        {"node": "bp", "port": "hits"},
                        {"node": "cv", "port": "quality"},
                        {"node": "p1", "port": "proteins"},
                    ]
                },
            },
        },
    }


async def run_once(pipeline_id: str, inputs=None) -> Dict[str, Any]:
    started = service.start_run(pipeline_id, "owner-1", inputs)
    status = await engine.execute_run(started["id"])
    detail = service.run_detail(started["id"])
    assert detail["status"] == status
    return detail


async def test_a_pipeline_runs_every_node_with_the_right_calls(env):
    pipeline_id = make_pipeline(two_genome_graph(), {"lambda.fasta": FASTA_NT, "t7.fasta": FASTA_NT2})
    detail = await run_once(pipeline_id)

    assert detail["status"] == "succeeded", detail
    assert {node_id: node["status"] for node_id, node in detail["nodes"].items()} == {
        node_id: "succeeded" for node_id in two_genome_graph()["nodes"]
    }
    calls = {(call["tool"], call["operation"]): call for call in env.calls}
    prodigal = calls[("prodigal", "predict")]
    assert prodigal["params"] == {
        "protein": "proteins.faa",
        "nucleotide": "genes.fna",
        "gff": "genes.gff",
        "mode": "meta",
        "translation_table": 11,
    }
    assert prodigal["input_file"].endswith("lambda.fasta") or prodigal["input_file"].endswith("t7.fasta")
    assert prodigal["timeout"] == 0
    blastp = calls[("blast", "blastp")]
    assert blastp["params"]["db"].endswith("/work/blast_db") and blastp["params"]["output"] == "blastp_hits.tsv"
    assert blastp["input_file"].endswith("/work/proteins.faa")
    mmseqs = calls[("mmseqs2", "easy_search")]
    assert mmseqs["input_file"] is None and mmseqs["params"]["search_type"] == 1
    assert mmseqs["params"]["query"].endswith("proteins.faa") and mmseqs["params"]["target"].endswith("proteins.faa")

    report_path = service.output_file(detail["id"], "rep", "report")
    assert report_path.read_text().startswith("# Report")
    assert "num_seqs" in env.last_prompt and "checkv_quality" in env.last_prompt
    assert "records: 2" in env.last_prompt  # FASTA evidence is summarized, not pasted
    # Each item says how it was made and how to read it; server paths are cut to their last part.
    assert "command: blast blastp proteins.faa -out result -evalue 10" in env.last_prompt
    assert "how to read: BLAST tabular (outfmt 6)" in env.last_prompt
    assert "hit rows: 1; queries with a hit: 1" in env.last_prompt
    assert str(env.root) not in env.last_prompt
    stats = service.preview(detail["id"], "stats", "stats")
    assert stats["kind"] == "text" and "num_seqs" in stats["text"]
    assert detail["nodes"]["p1"]["outputs"]["proteins"] == {"type": "fasta_aa", "kind": "file", "size": len(FASTA_AA), "name": "proteins.faa"}
    assert "work_dir" not in detail["nodes"]["p1"]["detail"]


async def test_a_failure_skips_everything_downstream_and_fails_the_run(env):
    env.fail["prodigal.predict"] = "prodigal crashed"
    pipeline_id = make_pipeline(two_genome_graph(), {"lambda.fasta": FASTA_NT, "t7.fasta": FASTA_NT2})
    detail = await run_once(pipeline_id)
    states = {node_id: node["status"] for node_id, node in detail["nodes"].items()}
    assert detail["status"] == "failed"
    assert states["p1"] == states["p2"] == "failed"
    assert states["db"] == states["bp"] == states["mm"] == states["rep"] == "skipped"
    assert states["stats"] == states["cv"] == "succeeded"
    assert detail["nodes"]["p1"]["error"] == "prodigal crashed"
    assert "boom" in detail["nodes"]["p1"]["detail"]["stderr_tail"]
    assert "p1" in detail["error"] and "rep" in detail["error"]


async def test_independent_nodes_run_in_parallel_up_to_the_limit(env, monkeypatch):
    monkeypatch.setattr(engine, "_settings_max_parallel", lambda: 2)
    env.delay = 0.05
    graph = {"schema": 1, "nodes": {"g": {"type": "input.genome", "params": {"path": "a.fasta"}}}}
    for index in range(5):
        graph["nodes"][f"s{index}"] = {
            "type": "virsorter2.run",
            "params": {"min_score": 0.1 * (index + 1)},
            "inputs": {"sequences": {"node": "g", "port": "file"}},
        }
    pipeline_id = make_pipeline(graph, {"a.fasta": FASTA_NT})
    detail = await run_once(pipeline_id)
    assert detail["status"] == "succeeded"
    assert env.peak == 2 and len(env.calls) == 5


async def test_identical_nodes_in_one_run_execute_once(env, monkeypatch):
    monkeypatch.setattr(engine, "_settings_max_parallel", lambda: 1)
    graph = {"schema": 1, "nodes": {"g": {"type": "input.genome", "params": {"path": "a.fasta"}}}}
    for index in range(3):
        graph["nodes"][f"c{index}"] = {"type": "checkv.end_to_end", "inputs": {"sequences": {"node": "g", "port": "file"}}}
    pipeline_id = make_pipeline(graph, {"a.fasta": FASTA_NT})
    detail = await run_once(pipeline_id)
    assert detail["status"] == "succeeded"
    assert len(env.calls) == 1
    assert sorted(node["status"] for node_id, node in detail["nodes"].items() if node_id != "g") == ["cached", "cached", "succeeded"]


async def test_unchanged_nodes_are_reused_on_the_next_run(env):
    pipeline_id = make_pipeline(two_genome_graph(), {"lambda.fasta": FASTA_NT, "t7.fasta": FASTA_NT2})
    first = await run_once(pipeline_id)
    calls_after_first = len(env.calls)
    second = await run_once(pipeline_id)
    assert first["status"] == second["status"] == "succeeded"
    assert len(env.calls) == calls_after_first, "every tool node should come from the cache"
    states = {node_id: node["status"] for node_id, node in second["nodes"].items()}
    assert states["p1"] == states["bp"] == "cached"
    assert states["rep"] == "succeeded"  # the report is always rewritten
    assert second["nodes"]["bp"]["detail"]["cached_from"]["run_id"] == first["id"]
    assert second["nodes"]["bp"]["detail"]["command"] == first["nodes"]["bp"]["detail"]["command"]
    assert "command: blast blastp proteins.faa" in env.last_prompt  # a reused node still tells the report how it ran

    detail = service.detail(pipeline_id)
    detail["draft"]["nodes"]["p2"]["params"] = {"mode": "single"}
    service.update(pipeline_id, graph=detail["draft"])
    third = await run_once(pipeline_id)
    rerun = {(call["tool"], call["operation"]) for call in env.calls[calls_after_first:]}
    assert ("prodigal", "predict") in rerun and ("blast", "blastp") in rerun and ("mmseqs2", "easy_search") in rerun
    assert ("blast", "makeblastdb") not in rerun  # built from p1, which did not change
    assert third["version"] == second["version"] + 1


async def test_a_changed_input_file_is_not_served_from_the_cache(env):
    pipeline_id = make_pipeline(two_genome_graph(), {"lambda.fasta": FASTA_NT, "t7.fasta": FASTA_NT2})
    await run_once(pipeline_id)
    before = len(env.calls)
    (service.session_dir(pipeline_id) / "uploads" / "lambda.fasta").write_text(FASTA_NT + ">seq3\nAAAA\n")
    await run_once(pipeline_id)
    rerun = {(call["tool"], call["operation"], call["input_file"]) for call in env.calls[before:]}
    assert any(tool == "prodigal" and path.endswith("lambda.fasta") for tool, _, path in rerun)
    assert not any(tool == "prodigal" and path.endswith("t7.fasta") for tool, _, path in rerun)


async def test_cancel_stops_running_nodes_and_marks_the_rest(env):
    env.block = asyncio.Event()
    graph = {
        "schema": 1,
        "nodes": {
            "g": {"type": "input.genome", "params": {"path": "a.fasta"}},
            "gm": {"type": "genomad.end_to_end", "inputs": {"sequences": {"node": "g", "port": "file"}}},
            "rep": {"type": "agent.report", "inputs": {"evidence": [{"node": "gm", "port": "results"}]}},
        },
    }
    pipeline_id = make_pipeline(graph, {"a.fasta": FASTA_NT})
    started = service.start_run(pipeline_id, "owner-1", None)
    task = asyncio.create_task(engine.execute_run(started["id"]))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if service.run_detail(started["id"])["nodes"]["gm"]["status"] == "running":
            break
    service.cancel_run(started["id"])
    assert await asyncio.wait_for(task, timeout=5) == "cancelled"
    detail = service.run_detail(started["id"])
    assert detail["status"] == "cancelled"
    assert detail["nodes"]["gm"]["status"] == "cancelled"
    assert detail["nodes"]["rep"]["status"] == "cancelled"
    with pytest.raises(service.PipelineError) as err:
        service.cancel_run(started["id"])
    assert err.value.status == 409


async def test_a_missing_output_fails_the_node_with_a_clear_message(env):
    env.skip_files.add("checkv_out/quality_summary.tsv")
    graph = {
        "schema": 1,
        "nodes": {
            "g": {"type": "input.genome", "params": {"path": "a.fasta"}},
            "cv": {"type": "checkv.end_to_end", "inputs": {"sequences": {"node": "g", "port": "file"}}},
        },
    }
    pipeline_id = make_pipeline(graph, {"a.fasta": FASTA_NT})
    detail = await run_once(pipeline_id)
    assert detail["nodes"]["cv"]["status"] == "failed"
    assert "checkv_out/quality_summary.tsv is missing" in detail["nodes"]["cv"]["error"]


async def test_inputs_must_be_files_of_this_pipeline(env, tmp_path):
    pipeline_id = make_pipeline(two_genome_graph(), {"lambda.fasta": FASTA_NT, "t7.fasta": FASTA_NT2})
    outside = tmp_path / "secret.fasta"
    outside.write_text(FASTA_NT)
    with pytest.raises(service.PipelineError) as err:
        service.start_run(pipeline_id, "owner-1", {"g1": str(outside)})
    assert err.value.status == 422 and err.value.extra["problems"][0]["code"] == "foreign_file"
    with pytest.raises(service.PipelineError) as err:
        service.start_run(pipeline_id, "owner-1", {"stats": "x"})
    assert err.value.status == 422


async def test_ncbi_nodes_download_through_sequence_fetch(env, monkeypatch):
    import tool_box.tools_impl.sequence_fetch as sequence_fetch

    seen = {}

    async def fake_fetch(**kwargs):
        seen.update(kwargs)
        target = service.session_dir(pipeline_id) / "tool_outputs" / "fetched.fasta"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(FASTA_NT)
        return {"success": True, "output_file": str(target), "record_count": 2, "provider": "ncbi_efetch"}

    monkeypatch.setattr(sequence_fetch, "sequence_fetch_handler", fake_fetch)
    graph = {
        "schema": 1,
        "nodes": {
            "dl": {"type": "ncbi.genome", "params": {"accessions": "NC_001416.1, NC_001604.1"}},
            "p": {"type": "prodigal.predict", "inputs": {"genome": {"node": "dl", "port": "genome"}}},
        },
    }
    pipeline_id = make_pipeline(graph, {})
    detail = await run_once(pipeline_id)
    assert detail["status"] == "succeeded", detail
    assert seen["accessions"] == ["NC_001416.1", "NC_001604.1"] and seen["format"] == "fasta"
    assert seen["session_id"] == engine.pipeline_session_id(pipeline_id)
    second = await run_once(pipeline_id)
    assert second["nodes"]["dl"]["status"] == "cached" and second["nodes"]["p"]["status"] == "cached"


async def test_a_download_reused_by_another_pipeline_is_copied_into_its_session(env, monkeypatch):
    import tool_box.tools_impl.sequence_fetch as sequence_fetch

    async def fake_fetch(**kwargs):
        target = Path(get_runtime_session_dir(kwargs["session_id"], create=True)) / "tool_outputs" / "fetched.fasta"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(FASTA_NT)
        return {"success": True, "output_file": str(target), "record_count": 2, "provider": "ncbi_efetch"}

    monkeypatch.setattr(sequence_fetch, "sequence_fetch_handler", fake_fetch)

    def graph_with(consumer: str, port: str) -> Dict[str, Any]:
        return {
            "schema": 1,
            "nodes": {
                "dl": {"type": "ncbi.genome", "params": {"accessions": "NC_001416.1"}},
                "use": {"type": consumer, "inputs": {port: {"node": "dl", "port": "genome"}}},
            },
        }

    first = make_pipeline(graph_with("prodigal.predict", "genome"), {})
    await run_once(first)
    second = make_pipeline(graph_with("seqkit.stats", "sequences"), {})
    env.calls.clear()
    detail = await run_once(second)

    assert detail["status"] == "succeeded", detail
    assert detail["nodes"]["dl"]["status"] == "cached"
    handed_over = Path(env.calls[0]["input_file"])
    own = service.session_dir(second).resolve()
    assert own in handed_over.resolve().parents and handed_over.read_text() == FASTA_NT


def test_runs_left_running_by_a_restart_are_failed(env):
    pipeline_id = make_pipeline(two_genome_graph(), {"lambda.fasta": FASTA_NT, "t7.fasta": FASTA_NT2})
    started = service.start_run(pipeline_id, "owner-1", None)
    with get_db() as conn:
        repo.init_node_runs(conn, started["id"], ["g1", "p1"])
        repo.mark_run_started(conn, started["id"])
        repo.set_node_running(conn, started["id"], "p1", None)
    assert service.recover_on_startup() == 1
    detail = service.run_detail(started["id"])
    assert detail["status"] == "failed" and "restart" in detail["error"]
    assert detail["nodes"]["p1"]["status"] == "failed"


def test_report_evidence_explains_empty_and_absent_outputs_and_condenses_hit_tables(tmp_path):
    empty = tmp_path / "fastani.txt"
    empty.write_text("")
    hits = tmp_path / "blastp_hits.tsv"
    hits.write_text(
        "".join(f"q{index % 7}\ts{index}\t35.0\t50\t30\t1\t1\t50\t1\t50\t{10.0 ** -(index % 9)}\t{20 + index}\n" for index in range(60))
    )
    text, cut = report.build_evidence(
        [
            {
                "label": "lambda vs T7",
                "port_title": "ANI",
                "node_type": "fastani.compare",
                "type": "table",
                "kind": "file",
                "path": str(empty),
                "reading": "FastANI reports nothing below about 80% ANI.",
                "command": "fastANI -q /app/runtime/x/lambda.fasta -r /app/runtime/x/t7.fasta -o /app/runtime/bio_tools/run/work/fastani.txt",
            },
            {
                "label": "lambda",
                "port_title": "Virus summary",
                "node_type": "genomad.end_to_end",
                "type": "table",
                "absent": True,
                "reading": "Not produced when no sequence is called viral.",
            },
            {
                "label": "lambda vs T7",
                "port_title": "Hits",
                "node_type": "blast.blastp",
                "type": "table",
                "kind": "file",
                "path": str(hits),
                "digest": "blast_tab",
            },
        ]
    )
    assert not cut
    assert "empty file (0 bytes)" in text and "how to read: FastANI reports nothing" in text
    assert "command: fastANI -q lambda.fasta -r t7.fasta -o fastani.txt" in text
    assert "not produced" in text and "Not produced when no sequence is called viral." in text
    assert "hit rows: 60; queries with a hit: 7; subjects hit: 60" in text
    assert "rows with E-value <= 1e-05: 25; queries with such a hit: 7" in text
    assert text.count("\nq") == 7  # the best hit of each query, nothing else


def test_report_evidence_summarizes_fasta_and_folders_and_marks_cuts(tmp_path):
    fasta = tmp_path / "g.fna"
    fasta.write_text(">a\nATGC\n>b\nGGGGCC\n")
    folder = tmp_path / "out"
    (folder / "final_prediction").mkdir(parents=True)
    (folder / "final_prediction" / "phatyp_prediction.tsv").write_text("Accession\tPred\nseq1\tvirulent\n")
    (folder / "midfolder.bin").write_bytes(b"\0" * 10)
    table = tmp_path / "big.tsv"
    table.write_text("x\t" * 60_000)
    text, cut = report.build_evidence(
        [
            {"label": "genome", "port_title": "file", "node_type": "input.genome", "type": "fasta_nt", "kind": "file", "path": str(fasta)},
            {"label": "phatyp", "port_title": "All results", "node_type": "phabox2.phatyp", "type": "directory", "kind": "dir", "path": str(folder)},
            {"label": "big", "port_title": "t", "node_type": "x", "type": "table", "kind": "file", "path": str(table)},
        ]
    )
    assert "records: 2" in text and "GC: 80.00%" in text
    assert "phatyp_prediction.tsv" in text and "virulent" in text
    assert cut and "(truncated" in text


async def test_the_lambda_t7_template_runs_end_to_end(env, monkeypatch):
    import tool_box.tools_impl.sequence_fetch as sequence_fetch
    from app.services.pipelines import templates

    genomes = {"NC_001416.1": FASTA_NT, "NC_001604.1": FASTA_NT2}

    async def fake_fetch(**kwargs):
        (accession,) = kwargs["accessions"]
        target = service.session_dir(pipeline_id) / "tool_outputs" / f"{kwargs['output_name']}.fasta"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(genomes[accession])
        return {"success": True, "output_file": str(target), "record_count": 1, "provider": "ncbi_efetch"}

    monkeypatch.setattr(sequence_fetch, "sequence_fetch_handler", fake_fetch)
    pipeline_id = service.create("owner-1", "Phage basics", "", None, "phage_lambda_t7")["id"]
    detail = await run_once(pipeline_id)

    assert detail["status"] == "succeeded", detail
    assert set(detail["nodes"]) == set(templates.graph_for("phage_lambda_t7")["nodes"])
    assert {node["status"] for node in detail["nodes"].values()} == {"succeeded"}
    assert Counter((call["tool"], call["operation"]) for call in env.calls) == {
        ("seqkit", "stats"): 2,
        ("prodigal", "predict"): 2,
        ("genomad", "end_to_end"): 2,
        ("checkv", "end_to_end"): 2,
        ("phabox2", "phatyp"): 2,
        ("fastani", "compare"): 1,
        ("blast", "makeblastdb"): 1,
        ("blast", "blastp"): 1,
        ("mmseqs2", "easy_search"): 1,
    }
    fastani = next(call for call in env.calls if call["tool"] == "fastani")
    assert fastani["params"]["query"].endswith("_lambda.fasta") and fastani["params"]["reference"].endswith("_t7.fasta")
    makeblastdb = next(call for call in env.calls if call["operation"] == "makeblastdb")
    assert makeblastdb["params"]["type"] == "prot"
    assert "lambda vs T7" in env.last_prompt and "virulent" in env.last_prompt
    assert env.last_prompt.count("## Evidence ") == 15
    assert "GC: " in env.last_prompt  # from the genome FASTA, which SeqKit's table does not carry
    assert "how to read: FastANI" in env.last_prompt
