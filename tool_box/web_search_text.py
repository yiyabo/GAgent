"""Model-facing web_search text that depends on the configured default provider.

The schema descriptions say how long a call takes and how to batch. That is true of
the builtin provider (one call is minutes), and wrong for Tavily (seconds, one unit
of a limited quota per query). Kept free of package imports so both schema homes
can read it at import time.
"""

from __future__ import annotations

import os

TAVILY_DESCRIPTION = (
    "Web search. The default provider is `tavily`, a fast search API: one query takes a few "
    "seconds and returns up to `max_results` results, each with a title, URL and a short snippet "
    "(not the full page). Read a promising result with `url_fetch` or `document_reader` before "
    "relying on it. Each query spends one unit of a limited search quota, so use `query` for a "
    "single lookup and `queries` (2-6 entries, run together) only when the lookups are genuinely "
    "different. `provider='builtin'` is a slower, deeper search (DashScope, 1-8 minutes per call) "
    "that returns a written answer with citations; use it only when a synthesized answer is worth the wait."
)

TAVILY_PROVIDER_PARAM = (
    "Optional override: `tavily` (fast, the default), `builtin` (DashScope web_search: slow but "
    "returns a written answer), or `perplexity`"
)


def default_provider() -> str:
    # Same variable `app.config.search_config` reads. Not `get_search_settings()`:
    # that can raise in production mode, and this runs while the schemas import.
    return (os.getenv("DEFAULT_WEB_SEARCH_PROVIDER") or "builtin").strip().lower()


def search_description(builtin_text: str) -> str:
    return TAVILY_DESCRIPTION if default_provider() == "tavily" else builtin_text


def provider_param_description(builtin_text: str) -> str:
    return TAVILY_PROVIDER_PARAM if default_provider() == "tavily" else builtin_text
