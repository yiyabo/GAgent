"""Tool results must enter the prompt bounded.

Measured 2026-09-27 on `.8`: one `web_search` result is ~9,578 chars (~3.2k
tokens). That is *under* `MAX_TOOL_RESULT_TEXT_CHARS` (12,000), so the size gate
let it through whole — and it was then re-sent on every later iteration of the
loop. t29_lit_uniprot_p53 spent 598,078 tokens over 20 iterations on a single
literature task, with a per-iteration prompt of ~27k-35k tokens.

The gate also had a second hole: when no per-tool compactor existed, the
oversized raw text was returned unchanged, so any tool without a branch could
put an unbounded blob in the prompt.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from app.services.deep_think import dispatch
from app.services.deep_think_agent import DeepThinkAgent


def _text(tool_name: str, result: Any) -> str:
    return DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name=tool_name,
        result=result,
        success=True,
        error=None,
    )


def _long_search_result(answer_chars: int = 20_000, results_count: int = 9) -> Dict[str, Any]:
    return {
        "query": "lung cancer immunotherapy 2026",
        "provider": "builtin",
        "success": True,
        "response": "A" * answer_chars,
        "results": [
            {"title": f"Paper {i}", "url": f"https://x/{i}", "snippet": "s" * 900}
            for i in range(results_count)
        ],
        "raw_provider_envelope": {"huge": "z" * 40_000},
    }


class TestWebSearchIsAlwaysCompacted:
    def test_a_search_result_under_the_size_cap_is_still_compacted(self) -> None:
        """~6.5k chars is under the 12k gate — the gate must not be the only trigger.

        Shaped like the real payload measured on `.8`: a ~2k-token answer plus a
        few result entries, which the 12k gate waves straight through.
        """
        result = {
            "query": "lung cancer immunotherapy 2026",
            "provider": "builtin",
            "success": True,
            "response": "A" * 5_000,
            "results": [
                {"title": f"Paper {i}", "url": f"https://x/{i}", "snippet": "s" * 400}
                for i in range(3)
            ],
        }
        raw_len = len(
            json.dumps(
                {"success": True, "tool": "web_search", "result": result},
                ensure_ascii=False,
            )
        )
        assert raw_len < DeepThinkAgent.MAX_TOOL_RESULT_TEXT_CHARS, raw_len

        payload = json.loads(_text("web_search", result))

        assert payload["result"]["llm_compacted"] is True
        assert len(payload["result"]["answer"]) <= dispatch._WEB_SEARCH_ANSWER_CHARS

    def test_the_provider_envelope_never_reaches_the_prompt(self) -> None:
        payload = json.loads(_text("web_search", _long_search_result()))

        assert "raw_provider_envelope" not in payload["result"]

    def test_the_answer_is_kept_but_capped_and_flagged(self) -> None:
        payload = json.loads(_text("web_search", _long_search_result(answer_chars=20_000)))

        assert len(payload["result"]["answer"]) == dispatch._WEB_SEARCH_ANSWER_CHARS
        assert payload["result"]["answer_truncated"] is True

    def test_a_short_answer_is_passed_through_untruncated(self) -> None:
        result = _long_search_result(answer_chars=120)
        payload = json.loads(_text("web_search", result))

        assert payload["result"]["answer"] == "A" * 120
        assert "answer_truncated" not in payload["result"]

    def test_results_are_capped_and_trimmed(self) -> None:
        payload = json.loads(_text("web_search", _long_search_result()))

        items = payload["result"]["results"]
        assert len(items) == 5
        assert all(len(item["snippet"]) <= dispatch._WEB_SEARCH_SNIPPET_CHARS for item in items)
        assert items[0]["url"] == "https://x/0"

    def test_the_query_and_provider_survive(self) -> None:
        payload = json.loads(_text("web_search", _long_search_result()))

        assert payload["result"]["query"] == "lung cancer immunotherapy 2026"
        assert payload["result"]["provider"] == "builtin"

    def test_a_non_dict_result_is_left_alone(self) -> None:
        payload = json.loads(_text("web_search", ["not", "a", "dict"]))

        assert payload["result"] == ["not", "a", "dict"]


class TestOversizedResultsWithoutACompactor:
    def test_a_tool_with_no_branch_is_clipped(self) -> None:
        result = {"payload": "x" * 80_000}

        text = _text("url_fetch", result)

        assert len(text) < 80_000
        assert "clipped from" in text

    def test_a_small_result_with_no_branch_is_untouched(self) -> None:
        result = {"payload": "small"}

        payload = json.loads(_text("url_fetch", result))

        assert payload["result"] == result
        assert "clipped" not in _text("url_fetch", result)

    def test_the_clip_keeps_the_head_of_the_content(self) -> None:
        result = {"payload": "HEAD" + "x" * 80_000}

        text = _text("url_fetch", result)

        assert "HEAD" in text

    def test_a_compacted_tool_is_not_clipped_instead(self) -> None:
        """A dedicated branch wins over the generic clip."""
        payload = json.loads(_text("web_search", _long_search_result()))

        assert payload["result"]["llm_compacted"] is True
        assert "clipped from" not in json.dumps(payload)
