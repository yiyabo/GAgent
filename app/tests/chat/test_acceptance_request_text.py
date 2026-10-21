"""Acceptance reads what the user wrote, not what the chat layer appended to it.

With an attachment, the chat layer adds the file list and a long policy text ("sheet", "report", file paths) to
the message. Acceptance used to run its keyword check and its extraction gate on that combined text, so a plain
question such as "what is the GC content of ctg05?" was treated as a request for files, the model invented a
required output, and the answer was prefixed with "delivery acceptance failed".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.deep_think import acceptance
from app.services.deep_think.acceptance import (
    _has_file_deliverable_intent,
    build_extraction_prompt,
    extract_acceptance_spec,
    tidy_extracted_spec,
)
from app.services.deep_think.checkpointing import finalize_output_spec
from app.services.deep_think.text_utils import (
    ATTACHMENT_NOTE_MARKER,
    _derive_expected_outputs,
    attachment_names,
    user_request_text,
)
from app.services.plans.output_spec import OutputSpec, RequiredOutput

ATTACHMENTS = (
    f"{ATTACHMENT_NOTE_MARKER}\n"
    "- genome_gc.fasta (file): /app/runtime/session_x/uploads/genome_gc.fasta\n"
    "\n=== CURRENT ATTACHMENT POLICY (CRITICAL) ===\n"
    "- The files listed above are the ONLY user data files authorized for this turn.\n"
    "- SPREADSHEET SHEET POLICY (CRITICAL):\n"
    "  * If the user names a sheet (e.g. Sheet1 / 主表 / 某工作表), analyze ONLY that sheet.\n"
    "  * When reporting, state which sheet(s) were analyzed.\n"
)
QUESTION = "What is the GC content of contig ctg05 in genome_gc.fasta? Give the percentage to one decimal place."
FILE_ASKS = [
    "把 contigs_mixed.fasta 里长度不少于 3000 bp 的序列筛出来，保存成一个新的 FASTA 文件，并告诉我保留了多少条。",
    "Randomly subsample 100 reads from reads_1.fastq using seed 42 and save them as a new FASTQ file.",
    "对 genome_small.fasta 做细菌基因预测：把蛋白序列存成文件，并告诉我预测到多少条基因（CDS）。",
    "Convert aligned.sam into a coordinate-sorted, indexed BAM file, then tell me how many reads are mapped.",
    "用 prodigal 预测一下 genome_mid.fasta 的基因，把蛋白序列存成文件。",
    "Randomly pick 50 reads from reads_long.fastq (seed 7) and save them to a new file.",
    "把 contigs_mixed.fasta 里长度不少于 3000 bp 的序列筛出来，存成新的 FASTA 文件，并告诉我保留几条。",
    "画两张图并写报告",
]
QUESTIONS = [
    QUESTION,
    "帮我统计一下上传的 contigs.fasta：一共多少条序列、总长度是多少、N50 是多少？",
    "Map reads_single.fastq to ref_5kb.fasta with minimap2. How many reads mapped to the reference?",
    "Annotate genome_bakta.fasta with Bakta using the light database. How many CDS features did it find?",
    "用 CheckV 评估 viral_contigs.fasta 里这些病毒 contig 的完整度和污染情况，告诉我一共评估了几条 contig。",
    "统计一下 sample_R1.fastq：reads 数和平均读长是多少？",
    "筛选长度 ≥1000 bp 的序列，告诉我留下几条。",
    "Start a Bakta annotation of genome_bg.fasta (light database) as a background job and tell me when it is finished.",
]


def test_the_user_request_is_cut_before_the_appended_blocks():
    assert user_request_text(QUESTION + ATTACHMENTS) == QUESTION
    assert user_request_text(QUESTION) == QUESTION
    grounded = QUESTION + "\n\n=== GROUNDED NUMBERED REFERENCES (CRITICAL — DO NOT SUBSTITUTE) ===\nx"
    assert user_request_text(grounded) == QUESTION
    assert user_request_text("") == ""
    assert user_request_text(None) == ""


def test_attachment_names_come_from_the_attachment_list_only():
    text = QUESTION + ATTACHMENTS.replace(
        "\n=== CURRENT",
        "- second.bam (file): /app/runtime/session_x/uploads/second.bam\n\n=== CURRENT",
    )
    assert attachment_names(text) == ["genome_gc.fasta", "second.bam"]
    assert attachment_names(QUESTION) == []


def test_the_marker_is_the_one_the_chat_layer_writes():
    root = Path(__file__).resolve().parents[2] / "routers" / "chat"
    for name in ("stream_context.py", "routes.py"):
        assert "User-uploaded attachments:" in (root / name).read_text(encoding="utf-8")
    assert ATTACHMENT_NOTE_MARKER == "\n\n📎 User-uploaded attachments:"


def test_the_keyword_check_ignores_the_attachment_text():
    assert _derive_expected_outputs(QUESTION + ATTACHMENTS) == []
    assert _derive_expected_outputs("把结果导出成 csv" + ATTACHMENTS) == ["data"]


@pytest.mark.parametrize("request_text", FILE_ASKS)
def test_a_request_for_a_file_is_recognised(request_text):
    assert _has_file_deliverable_intent(user_request_text(request_text + ATTACHMENTS))


@pytest.mark.parametrize("request_text", QUESTIONS)
def test_a_question_is_not_a_request_for_a_file(request_text):
    assert not _has_file_deliverable_intent(user_request_text(request_text + ATTACHMENTS))


class _Llm:
    def __init__(self, reply: str = '{"required_outputs": []}'):
        self.calls = 0
        self.prompts = []
        self._reply = reply

    async def stream_chat_async(self, prompt: str = "", **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        yield self._reply


def _agent(llm):
    return SimpleNamespace(llm_client=llm, _request_tier=lambda: "execute")


def _extract(llm, query):
    return asyncio.run(extract_acceptance_spec(_agent(llm), query))


def test_a_question_with_an_attachment_makes_no_extraction_call(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    llm = _Llm('{"required_outputs": [{"kind": "data"}]}')
    assert _extract(llm, QUESTION + ATTACHMENTS) is None
    assert llm.calls == 0


def test_a_request_for_a_file_with_an_attachment_is_extracted_from_the_users_words(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    llm = _Llm('{"required_outputs": [{"kind": "data", "extensions": [".faa"]}]}')
    spec = _extract(llm, FILE_ASKS[4] + ATTACHMENTS)
    assert spec is not None and spec.required_outputs[0].extensions == [".faa"]
    prompt = llm.prompts[0]
    assert "SPREADSHEET SHEET POLICY" not in prompt and "CURRENT ATTACHMENT POLICY" not in prompt
    assert "Input files already provided" in prompt and "genome_gc.fasta" in prompt
    assert FILE_ASKS[4] in prompt


def test_an_empty_declaration_means_no_file_and_is_not_an_error(monkeypatch, caplog):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    caplog.set_level("WARNING", logger=acceptance.logger.name)
    assert _extract(_Llm(), FILE_ASKS[1] + ATTACHMENTS) is None
    assert not [r for r in caplog.records if "invalid JSON" in r.getMessage()]


def test_one_requested_file_is_one_declaration():
    spec = OutputSpec(required_outputs=[
        RequiredOutput(kind="data", extensions=[".fasta"]),
        RequiredOutput(kind="other"),
    ])
    tidy = tidy_extracted_spec(spec, [])
    assert [o.kind for o in tidy.required_outputs] == ["data"]


def test_a_lone_other_declaration_is_kept_when_it_is_all_there_is():
    spec = OutputSpec(required_outputs=[RequiredOutput(kind="other")])
    assert tidy_extracted_spec(spec, []) is spec


def test_an_unchanged_input_file_is_not_a_deliverable():
    spec = OutputSpec(required_outputs=[
        RequiredOutput(kind="data", target_path="results/genome_gc.fasta"),
        RequiredOutput(kind="data", target_path="results/genome_gc.fasta", in_place=True),
    ])
    tidy = tidy_extracted_spec(spec, ["genome_gc.fasta"])
    assert [o.in_place for o in tidy.required_outputs] == [True]
    only_input = OutputSpec(required_outputs=[RequiredOutput(kind="data", target_path="genome_gc.fasta")])
    assert tidy_extracted_spec(only_input, ["genome_gc.fasta"]) is None


def test_the_prompt_tells_the_model_a_question_is_not_a_file():
    prompt = build_extraction_prompt(QUESTION + ATTACHMENTS)
    assert '{"required_outputs": []}' in prompt
    assert "never add a second declaration for the same file" in prompt


def _failed_agent():
    return SimpleNamespace(
        _expected_outputs_current=[],
        _produced_deliverable_paths=[],
        _acceptance_spec=None,
        _output_verification={"authoritative": True, "status": "failed"},
        _execution_issues=[],
        _plan_agent_completion=False,
    )


def test_the_banner_language_follows_the_users_words_not_the_attachment_text():
    # The attachment policy text contains Chinese; an English question must still get an English banner.
    answer, _ = asyncio.run(finalize_output_spec(_failed_agent(), QUESTION + ATTACHMENTS + "主表", "42%"))
    assert answer.startswith("Output acceptance did not pass")
    zh, _ = asyncio.run(finalize_output_spec(_failed_agent(), "把蛋白序列存成文件" + ATTACHMENTS, "ok"))
    assert zh.startswith("本轮交付验收未通过")


def test_the_json_roundtrip_of_a_tidied_spec_is_valid():
    spec = tidy_extracted_spec(
        OutputSpec(required_outputs=[RequiredOutput(kind="data", extensions=[".faa"]), RequiredOutput(kind="other")]), []
    )
    assert json.loads(json.dumps(spec.to_dict()))["required_outputs"][0]["extensions"] == [".faa"]
