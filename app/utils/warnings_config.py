#!/usr/bin/env python3
"""
warningconfiguration

mediumwarning, : 
1. warning
2. warning
3. 
"""

import functools
import logging
import warnings
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def configure_warnings():
    """configurationwarning"""

    warnings.resetwarnings()

    warnings.simplefilter("default")

    third_party_ignores = [
        ("ignore", None, DeprecationWarning, "pkg_resources.*"),
        ("ignore", None, DeprecationWarning, "setuptools.*"),
        ("ignore", None, DeprecationWarning, "sqlite3.*"),
        ("ignore", None, PendingDeprecationWarning, "asyncio.*"),
        ("ignore", None, UserWarning, "numpy.*"),
        ("ignore", None, UserWarning, "matplotlib.*"),
        ("ignore", None, ResourceWarning, None),
    ]

    for action, message, category, module in third_party_ignores:
        warnings.filterwarnings(action, message=message, category=category, module=module)

    warnings.filterwarnings("always", category=DeprecationWarning, module="app.*")
    warnings.filterwarnings("always", category=DeprecationWarning, module="tests.*")

    logger.info("Warning filters configured")


def suppress_warnings(*categories):
    """: typewarning"""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with warnings.catch_warnings():
                for category in categories:
                    warnings.simplefilter("ignore", category)
                return func(*args, **kwargs)

        return wrapper

    return decorator


def track_warnings(func):
    """: executewarning"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with warnings.catch_warnings(record=True) as warning_list:
            warnings.simplefilter("always")  # warning

            result = func(*args, **kwargs)

            if warning_list:
                logger.warning(f"Function {func.__name__} generated {len(warning_list)} warnings:")
                for w in warning_list:
                    logger.warning(f"  {w.category.__name__}: {w.message} ({w.filename}:{w.lineno})")

            return result

    return wrapper


class WarningContext:
    """warning"""

    def __init__(self, action: str = "ignore", category: type = Warning, message: str = "", module: str = ""):
        self.action = action
        self.category = category
        self.message = message
        self.module = module

    def __enter__(self):
        self.warnings_context = warnings.catch_warnings()
        self.warnings_context.__enter__()
        warnings.filterwarnings(self.action, message=self.message, category=self.category, module=self.module)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.warnings_context.__exit__(exc_type, exc_val, exc_tb)


def get_warning_stats() -> Dict[str, Any]:
    """getwarningconfiguration"""
    filters = warnings.filters

    stats = {
        "total_filters": len(filters),
        "filter_actions": {},
        "filter_categories": {},
    }

    for filter_spec in filters:
        action = filter_spec[0]
        category = filter_spec[2]

        stats["filter_actions"][action] = stats["filter_actions"].get(action, 0) + 1

        if category:
            category_name = category.__name__
            stats["filter_categories"][category_name] = stats["filter_categories"].get(category_name, 0) + 1

    return stats


configure_warnings()
