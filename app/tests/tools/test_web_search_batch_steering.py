"""The web_search offer must steer the model into batching its lookups.

Measured 2026-09-27 on `.8` (`probe_search_parallel.py`):

    one call, 1 query           121.58s
    one call, 3 queries         240.48s
    three separate calls        630.99s

So several subqueries in one call cost about one round trip, while searching
once per iteration costs the sum. But the model never emitted `queries` — every
probe produced `{"query": ...}` — because the offer framed `queries` as an
optional extra for "broad comparison tasks" while `query` was the required
field. These tests pin the steering text so it does not drift back to that
framing; whether it actually changes the model's behaviour is a separate,
empirical question answered by re-running t29.
"""

from __future__ import annotations

from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT

WEB_SEARCH = NATIVE_TOOL_CONTENT["web_search"]
DESCRIPTION = WEB_SEARCH["description"]
PARAMS = WEB_SEARCH["parameters"]
QUERIES = PARAMS["properties"]["queries"]


class TestWebSearchBatchingSteer:
    def test_the_offer_tells_the_model_to_batch(self) -> None:
        lowered = DESCRIPTION.lower()

        assert "queries" in lowered, DESCRIPTION
        assert "one call" in lowered, DESCRIPTION
        assert "batch" in lowered, DESCRIPTION

    def test_the_offer_names_the_cost_of_a_call(self) -> None:
        """The whole reason to batch is the per-call cost, so it has to be stated."""
        assert "two minutes" in DESCRIPTION.lower()

    def test_the_offer_says_they_run_together(self) -> None:
        lowered = DESCRIPTION.lower()

        assert "run together" in lowered or "one round trip" in lowered, DESCRIPTION

    def test_the_queries_property_prefers_batching_over_repeats(self) -> None:
        description = QUERIES["description"].lower()

        assert "prefer" in description
        assert "single-query" in description or "single query" in description

    def test_the_bounds_still_match_what_the_handler_accepts(self) -> None:
        assert QUERIES["minItems"] == 2
        assert QUERIES["maxItems"] == 6

    def test_query_is_offered_but_not_required_so_batched_calls_validate(self) -> None:
        """The steer and argument validation only agree if `query` is optional.

        Replay of 571 production checkpoints (2026-10-09, LOCAL_INFRA §111): 18 of
        21 real web_search calls were `queries`-only — exactly what the steer asks
        for — and `required: ["query"]` would have made argument validation reject
        them. An argument-less call is still caught, by the handler's own
        `missing_query` answer (handler.py, added 2026-09-26), not by the schema.
        """
        assert "query" in PARAMS["properties"]
        assert PARAMS["required"] == []
