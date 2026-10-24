"""Parameters the models guess for bio_tools operations: synonyms, retry details, typed values.

The evaluation rounds showed 85 refused steps; the models pass the real command-line flags of the programs
(seqkit seq --min-len, seqtk sample -s, samtools view -c/-F, prodigal -p/-g). The refusal used to say only
``Invalid/unsafe parameter: X``; the accepted names were in the stored result but the chat layer dropped them.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
from pathlib import Path

import pytest

from tool_box.bio_tools import native_exec, param_hints

handler = importlib.import_module("tool_box.bio_tools.bio_tools_handler")
CONFIG = native_exec.load_tools_config(str(Path(native_exec.__file__).parent / "tools_config.json"))


def _resolve(tool, op, params):
    return param_hints.resolve(CONFIG, tool, op, params)


# --------------------------------------------------------------------------- synonyms

@pytest.mark.parametrize(
    "tool,op,given,expected",
    [
        ("seqkit", "seq", {"min_len": 3000, "output": "o.fa"}, {"min_length": 3000, "output": "o.fa"}),
        ("seqkit", "seq", {"minimum_length": 3000}, {"min_length": 3000}),
        ("seqkit", "seq", {"--min-len": 3000}, {"min_length": 3000}),
        ("seqkit", "seq", {"MAX_LEN": 9000}, {"max_length": 9000}),
        ("seqtk", "sample", {"random_seed": 7, "fraction": 100}, {"seed": 7, "fraction": 100}),
        ("samtools", "count", {"F": 4}, {"exclude_flags": 4}),
        ("samtools", "count", {"f": 4}, {"require_flags": 4}),
        ("samtools", "view", {"mapq": 30}, {"min_mapq": 30}),
        ("prodigal", "predict", {"output": "p.faa"}, {"protein": "p.faa"}),
        ("prodigal", "meta", {"output_protein": "p.faa"}, {"protein": "p.faa"}),
    ],
)
def test_a_synonym_is_replaced_by_the_declared_name(tool, op, given, expected):
    params, renames = _resolve(tool, op, given)
    assert params == expected
    assert renames and all(target in expected for target in renames.values())


def test_a_declared_name_wins_over_its_synonym():
    params, renames = _resolve("seqkit", "seq", {"min_len": 1, "min_length": 2})
    assert params == {"min_len": 1, "min_length": 2} and renames == {}
    assert param_hints.unknown_keys(CONFIG, "seqkit", "seq", params) == ["min_len"]


def test_an_ambiguous_name_is_not_guessed():
    params, renames = _resolve("samtools", "view", {"flag": 4})
    assert params == {"flag": 4} and renames == {}


def test_declared_and_reserved_names_pass_through():
    given = {"output": "o.bam", "threads": 2, "input_file": "/x", "timeout": 5}
    assert _resolve("samtools", "view", given) == (given, {})


# --------------------------------------------------------------------------- what a refusal tells the caller

def test_the_hint_lists_every_accepted_parameter_and_the_sibling_operations():
    text = param_hints.hint(CONFIG, "samtools", "view", ["count"])
    assert "no parameter 'count'" in text
    for name in ("threads", "output", "exclude_flags", "require_flags", "min_mapq"):
        assert name in text
    assert "count" in text.split("Other operations of samtools:")[1]
    assert "execute_code" in text


def test_the_hint_for_a_server_database_tool_says_not_to_pass_one():
    assert "do not pass a database parameter" in param_hints.hint(CONFIG, "bakta", "annotate", ["db"])


def test_details_carry_type_default_and_choices():
    rows = {row["name"]: row for row in param_hints.details(CONFIG, "prodigal", "predict")}
    assert rows["mode"]["allowed"] == ["single", "meta"] and rows["mode"]["default"] == "single"
    assert rows["translation_table"]["type"] == "integer" and rows["translation_table"]["default"] == 11
    assert rows["protein"]["required"] is False


@pytest.mark.parametrize(
    "tool,op,params,message",
    [
        ("seqkit", "seq", {"min_length": "abc"}, "min_length must be an integer"),
        ("seqtk", "sample", {"seed": 1.5}, "seed must be an integer"),
        ("seqtk", "sample", {"fraction": "many"}, "fraction must be a number"),
        ("prodigal", "predict", {"mode": "fast"}, "mode must be one of single, meta"),
        ("samtools", "view", {"min_mapq": True}, "min_mapq must be an integer"),
    ],
)
def test_wrong_types_and_choices_are_reported(tool, op, params, message):
    assert message in (param_hints.check_values(CONFIG, tool, op, params) or "")


@pytest.mark.parametrize(
    "tool,op,params",
    [
        ("seqkit", "seq", {"min_length": 3000}),
        ("seqkit", "seq", {"min_length": "3000"}),
        ("seqkit", "seq", {"min_length": -1}),
        ("seqtk", "sample", {"fraction": 0.1}),
        ("seqtk", "sample", {"fraction": "1000"}),
        ("prodigal", "predict", {"mode": "META"}),
        ("blast", "blastp", {"evalue": 1e-5}),
        ("blast", "blastp", {"evalue": "1e-05"}),
        ("blast", "blastp", {"evalue": "2.5E-3"}),
    ],
)
def test_valid_values_pass(tool, op, params):
    assert param_hints.check_values(CONFIG, tool, op, params) is None


def test_a_bare_database_name_is_refused_with_the_reason():
    key, message = param_hints.relative_prefix_error(CONFIG, "blast", "blastn", {"db": "nt"})
    assert key == "db"
    assert "absolute path prefix" in message and "NOT installed" in message
    assert param_hints.relative_prefix_error(CONFIG, "blast", "blastn", {"db": "/app/runtime/s/db"}) is None


def test_a_name_is_what_the_database_builders_take():
    # makeblastdb and hmmpress write `/work/{db}`: the value names the new database, so a bare name is right.
    assert param_hints.relative_prefix_error(CONFIG, "blast", "makeblastdb", {"db": "lambda_db", "type": "prot"}) is None
    assert param_hints.relative_prefix_error(CONFIG, "hmmer", "hmmpress", {"db": "profiles.hmm"}) is None
    # The operations that read a database still need the absolute prefix of one built in the session.
    assert param_hints.relative_prefix_error(CONFIG, "blast", "blastp", {"db": "lambda_db"})[0] == "db"
    assert param_hints.relative_prefix_error(CONFIG, "hmmer", "hmmscan", {"db": "profiles.hmm"})[0] == "db"


# --------------------------------------------------------------------------- through the handler

def _call(tmp_path, tool, op, params):
    source = tmp_path / "in.sam"
    source.write_text("@HD\tVN:1.6\n")
    return asyncio.run(handler.bio_tools_handler(tool_name=tool, operation=op, input_file=str(source), params=params))


def test_a_refused_call_returns_what_the_operation_accepts(tmp_path):
    result = _call(tmp_path, "samtools", "view", {"count": True, "output": "o.bam"})
    assert result["success"] is False and result["error_code"] == "invalid_parameter"
    assert result["error"] == "Invalid/unsafe parameter: count"
    assert result["unknown_params"] == ["count"]
    assert {"exclude_flags", "require_flags", "min_mapq", "output", "threads"} <= set(result["accepted_params"])
    assert "no parameter 'count'" in result["param_hint"]


def test_all_unknown_names_are_reported_together(tmp_path):
    result = _call(tmp_path, "seqkit", "stats", {"output": "o", "all": True})
    assert result["unknown_params"] == ["output", "all"]
    assert result["accepted_params"] == []
    assert "stats_all" in result["param_hint"]


def test_a_synonym_is_accepted_and_the_rewrite_is_reported(tmp_path, monkeypatch):
    seen = {}

    async def fake_once(**kwargs):
        seen.update(kwargs)
        return {"success": True, "tool": kwargs["tool_name"], "operation": kwargs["operation"]}

    monkeypatch.setattr(handler, "_execute_bio_tool_once", fake_once)
    result = _call(tmp_path, "seqkit", "seq", {"min_len": "3000", "output": "o.fa"})
    assert result["success"] is True
    assert result["param_renames"] == {"min_len": "min_length"}
    assert seen["params"]["min_length"] == "3000" and "min_len" not in seen["params"]


def test_a_wrong_type_is_refused_before_anything_runs(tmp_path):
    result = _call(tmp_path, "seqtk", "sample", {"fraction": 10, "output": "o.fq", "seed": "abc"})
    assert result["error_code"] == "invalid_parameter" and "seed must be an integer" in result["error"]


def test_a_bare_database_name_is_refused_in_native_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    result = _call(tmp_path, "blast", "blastn", {"db": "nt", "output": "o.tsv"})
    assert result["error_code"] == "invalid_parameter"
    assert "absolute path prefix" in result["error"] and "NOT installed" in result["error"]


def test_the_chat_layer_keeps_the_retry_details():
    from app.routers.chat.tool_results import sanitize_tool_result

    raw = {
        "success": False, "tool": "samtools", "operation": "view", "error": "Invalid/unsafe parameter: count",
        "error_code": "invalid_parameter", "accepted_params": ["output"], "unknown_params": ["count"],
        "param_details": [{"name": "output"}], "param_hint": "samtools.view has no parameter 'count'.",
        "retry_hint": {"tool_name": "samtools"}, "param_renames": {"a": "b"},
    }
    kept = sanitize_tool_result("bio_tools", raw)
    for key in ("accepted_params", "unknown_params", "param_details", "param_hint", "retry_hint", "param_renames"):
        assert kept[key] == raw[key], key


# --------------------------------------------------------------------------- the commands the new parameters produce

@pytest.fixture()
def dirs(tmp_path):
    read_root = tmp_path / "runtime"
    work = read_root / "bio_tools" / "_native" / "runs" / "aaaaaaaaaaaa" / "work"
    work.mkdir(parents=True)
    (read_root / "session_x").mkdir()
    source = read_root / "session_x" / "in.fa"
    source.write_text(">a\nACGT\n")
    return {"read_root": read_root, "work": work, "source": source}


def _command(dirs, tool, op, params=None):
    return native_exec.build_job(
        CONFIG, tool=tool, operation=op, input_file=str(dirs["source"]), output_file=None, params=params or {},
        timeout=None, work_dir=str(dirs["work"]), read_roots=[str(dirs["read_root"])],
    )["command"]


def test_seqkit_seq_filters_by_length_and_is_unchanged_without_it(dirs):
    assert "-m 3000 -M -1" in _command(dirs, "seqkit", "seq", {"output": "o.fa", "min_length": 3000})
    assert "-m -1 -M -1" in _command(dirs, "seqkit", "seq", {"output": "o.fa"})


def test_seqtk_sample_takes_a_seed_and_keeps_its_own_default(dirs):
    assert "seqtk sample -s 42 " in _command(dirs, "seqtk", "sample", {"fraction": "0.5", "output": "o.fq", "seed": 42})
    assert "seqtk sample -s 11 " in _command(dirs, "seqtk", "sample", {"fraction": "0.5", "output": "o.fq"})


def test_samtools_view_and_count_take_flag_filters(dirs):
    view = _command(dirs, "samtools", "view", {"output": "o.bam"})
    assert "-F 0 -f 0 -q 0" in view and "-b" in view
    count = _command(dirs, "samtools", "count", {"output": "n.txt", "exclude_flags": 4})
    assert "samtools view -c" in count and "-F 4 -f 0 -q 0" in count and "n.txt" in count
    assert "samtools flagstat" in _command(dirs, "samtools", "flagstat", {"output": "f.txt"})


def test_seqkit_stats_all_adds_the_extended_columns(dirs):
    assert _command(dirs, "seqkit", "stats_all").startswith("seqkit stats -a ")
    assert _command(dirs, "seqkit", "stats").startswith("seqkit stats ")


def test_prodigal_predict_has_defaults_for_every_output_and_modes(dirs):
    plain = _command(dirs, "prodigal", "predict")
    assert "-p single -g 11 -f gff" in plain
    for name in ("genes.faa", "genes.fna", "genes.gff"):
        assert name in plain
    meta = _command(dirs, "prodigal", "predict", {"mode": "meta", "translation_table": 4, "protein": "p.faa"})
    assert "-p meta -g 4" in meta and "p.faa" in meta
    assert "-p meta" in _command(dirs, "prodigal", "meta")
    assert "genes.faa" in _command(dirs, "prodigal", "meta")


def test_the_new_flags_are_accepted_by_the_real_programs_when_they_are_installed(dirs, tmp_path):
    """Skipped where the programs are absent; the evaluation host runs the same commands for real."""
    import shutil

    if not (shutil.which("seqkit") and shutil.which("seqtk")):
        pytest.skip("seqkit and seqtk are not installed here")
    source = dirs["source"]
    source.write_text(">short\nACGT\n>long\n" + "ACGT" * 10 + "\n")
    run = subprocess.run(
        ["bash", "-c", _command(dirs, "seqkit", "seq", {"output": "o.fa", "min_length": 20})],
        cwd=str(dirs["work"]), capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    assert (dirs["work"] / "o.fa").read_text().count(">") == 1
