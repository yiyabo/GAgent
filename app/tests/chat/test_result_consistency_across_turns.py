"""Multi-turn results stay consistent, keep the requested format and cite sources."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.deep_think.acceptance import (
    build_extraction_prompt,
    earlier_user_requests,
    extract_acceptance_spec,
    tidy_extracted_spec,
)
from app.services.deep_think.prompts import (
    RECENT_ASSISTANT_CLIP_CHARS,
    _build_shared_strategy_block,
    _extract_history_messages,
)
from app.services.plans.output_spec import OutputSpec, RequiredOutput
from app.services.deep_think_agent import DeepThinkAgent
from app.services.prompt_policy import RESULT_CONSISTENCY_POLICY


async def _noop_tool_executor(_name, _params):
    return {"success": True}

TABLE_REPLY = "| Protein ID | Product | Source |\n" + "| YP_009217792.1 | tail fiber | NCBI |\n" * 300
LEDGER = "\n\n[Verified Execution Ledger run=r1] sequence_fetch x1 ok"


def test_the_latest_assistant_replies_keep_their_result_tables():
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "old " + "x" * 2000},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": TABLE_REPLY},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": TABLE_REPLY},
    ]
    messages = _extract_history_messages({"chat_history": history})
    assert len(messages[1]["content"]) == 503
    assert messages[3]["content"] == TABLE_REPLY[:RECENT_ASSISTANT_CLIP_CHARS] + "..."
    assert len(messages[5]["content"]) == RECENT_ASSISTANT_CLIP_CHARS + 3


def test_clipping_keeps_the_execution_ledger():
    history = [
        {"role": "assistant", "content": "y" * 9000 + LEDGER},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "short"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "short"},
    ]
    messages = _extract_history_messages({"chat_history": history})
    assert messages[0]["content"].endswith("..." + LEDGER)
    assert len(messages[0]["content"]) == 503 + len(LEDGER)


def test_brief_follow_ups_keep_their_short_history():
    history = [{"role": "assistant", "content": TABLE_REPLY}, {"role": "user", "content": "继续"}]
    context = {"chat_history": history, "intent_type": "execute_task", "brevity_hint": True}
    messages = _extract_history_messages(context)
    assert len(messages[0]["content"]) == 243


def test_the_strategy_prompt_carries_the_consistency_policy():
    agent = DeepThinkAgent(
        llm_client=SimpleNamespace(), available_tools=["web_search"], tool_executor=_noop_tool_executor
    )
    block = _build_shared_strategy_block(agent)
    assert RESULT_CONSISTENCY_POLICY in block
    for rule in ("v1 -> v2", "it is not FASTA", "PhageScope record", "your inference"):
        assert rule in RESULT_CONSISTENCY_POLICY


def test_earlier_user_requests_skip_the_current_turn_and_assistant_replies():
    history = [
        {"role": "user", "content": "以fasta格式给我"},
        {"role": "assistant", "content": ">seq"},
        {"role": "user", "content": "把你预测的protein id以表格形式给我"},
        {"role": "assistant", "content": "| id |"},
        {"role": "user", "content": "重新给我生成一份"},
    ]
    assert earlier_user_requests({"chat_history": history}, "重新给我生成一份") == [
        "以fasta格式给我",
        "把你预测的protein id以表格形式给我",
    ]
    assert earlier_user_requests(None, "x") == []


def test_the_extraction_prompt_sees_earlier_requests_and_the_table_rule():
    prompt = build_extraction_prompt("重新给我生成一份文件", ["把protein id以表格形式给我"])
    assert "把protein id以表格形式给我" in prompt
    assert "most recent format the user named" in prompt
    assert "FASTA is not a table" in prompt
    assert "Earlier user messages" not in build_extraction_prompt("生成一份文件")


def test_a_table_request_keeps_table_extensions():
    spec = OutputSpec(required_outputs=[RequiredOutput(kind="data", extensions=[".csv", ".tsv", ".xlsx"])])
    tidied = tidy_extracted_spec(spec, [], "把id以表格形式给我")
    assert tidied.required_outputs[0].extensions == [".csv", ".tsv", ".xlsx"]


def test_a_fasta_file_does_not_satisfy_a_table_request(tmp_path):
    from app.services.plans.output_spec import validate_output_spec

    (tmp_path / "tail_fibers.fasta").write_text(">YP_009217792.1\nMKV\n")
    spec = OutputSpec(required_outputs=[RequiredOutput(kind="data", extensions=[".csv", ".tsv", ".xlsx"])])
    spec = tidy_extracted_spec(spec, [], "把id以表格形式给我")
    fasta_only = validate_output_spec(spec, [str(tmp_path / "tail_fibers.fasta")], base_dir=tmp_path)
    (tmp_path / "tail_fibers.csv").write_text("protein_id\nYP_009217792.1\n")
    with_table = validate_output_spec(
        spec, [str(tmp_path / "tail_fibers.fasta"), str(tmp_path / "tail_fibers.csv")], base_dir=tmp_path
    )
    assert fasta_only["status"] == "failed"
    assert with_table["status"] == "passed"


class _Llm:
    def __init__(self, reply: str):
        self.prompts: list[str] = []
        self._reply = reply

    async def stream_chat_async(self, prompt: str = "", **kwargs):
        self.prompts.append(prompt)
        yield self._reply


def test_a_regenerate_request_inherits_the_earlier_table_format(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    llm = _Llm('{"required_outputs": [{"kind": "data", "extensions": [".csv", ".tsv", ".xlsx"]}]}')
    agent = SimpleNamespace(llm_client=llm, _request_tier=lambda: "execute")
    context = {"chat_history": [
        {"role": "user", "content": "把你预测的protein id以表格形式给我"},
        {"role": "assistant", "content": "| id |"},
    ]}
    spec = asyncio.run(extract_acceptance_spec(agent, "重新给我生成一份文件", context))
    assert "把你预测的protein id以表格形式给我" in llm.prompts[0]
    assert spec.required_outputs[0].extensions == [".csv", ".tsv", ".xlsx"]
