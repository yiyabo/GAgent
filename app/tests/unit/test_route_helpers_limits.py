from app.utils.route_helpers import sanitize_context_options


def test_sanitize_context_options_allows_100k_section_budget() -> None:
    sanitized = sanitize_context_options(
        {
            "max_chars": 200000,
            "per_section_max": 100000,
        }
    )

    assert sanitized["max_chars"] == 100000
    assert sanitized["per_section_max"] == 100000
