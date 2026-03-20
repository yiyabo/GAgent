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
    answer, refs = extract_answer_from_responses_payload(data, max_results=5)
    assert "sunny" in answer
    assert len(refs) == 1
    assert refs[0]["url"] == "https://weather.example/now"


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
    answer, refs = extract_answer_from_responses_payload(data, max_results=5)
    assert "a.example" in answer
    assert any("a.example" in r["url"] for r in refs)
