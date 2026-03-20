"""Unit tests for DashScope Responses API payload parsing (web_search)."""

from tool_box.tools_impl.web_search.providers.builtin import extract_answer_from_responses_payload


def test_extract_answer_from_responses_payload_output_text() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Today is sunny.",
                        "annotations": [
                            {
                                "url": "https://weather.example/now",
                                "title": "Example Weather",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    answer, refs, stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "sunny" in answer
    assert len(refs) == 1
    assert refs[0]["url"] == "https://weather.example/now"
    assert stats["from_annotations"] == 1


def test_extract_answer_falls_back_to_urls_in_text() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "See https://a.example/x for details.", "annotations": []}],
            }
        ]
    }
    answer, refs, stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "a.example" in answer
    assert any("a.example" in r["url"] for r in refs)
    assert stats["from_text"] >= 1


def test_extract_answer_nested_tool_block_url() -> None:
    """Citations sometimes live under non-message output items (tool / search blocks)."""
    data = {
        "output": [
            {
                "type": "custom_web_search",
                "results": [
                    {"title": "News", "url": "https://news.example/a", "snippet": "Line"},
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Brief summary without links.",
                        "annotations": [],
                    }
                ],
            },
        ]
    }
    answer, refs, stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "summary" in answer
    assert len(refs) == 1
    assert refs[0]["url"] == "https://news.example/a"
    assert stats["from_tree"] >= 1


def test_extract_markdown_link_from_text() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Read [Paper](https://paper.example/p.pdf) for details.",
                        "annotations": [],
                    }
                ],
            }
        ]
    }
    answer, refs, _stats = extract_answer_from_responses_payload(data, max_results=5)
    assert "Paper" in answer or "paper.example" in answer
    assert any(r["url"] == "https://paper.example/p.pdf" for r in refs)
