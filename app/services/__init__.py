"""Services package for business logic.

This package has been reorganized into subpackages to reduce top-level clutter.
To preserve backward compatibility for existing import paths like
`app.services.<module>`, we register lightweight submodule aliases that
point to the new locations under subpackages.
"""

from importlib import import_module
import sys as _sys


_ALIAS_MAP = {
    # foundation
    "settings": "app.services.foundation.settings",
    "config": "app.services.foundation.config",
    "logging_config": "app.services.foundation.logging_config",

    # llm
    "llm_service": "app.services.llm.llm_service",
    "llm_cache": "app.services.llm.llm_cache",

    # memory
    "memory_service": "app.services.memory.memory_service",
    "unified_cache": "app.services.memory.unified_cache",
}


def _register_aliases():
    pkg_name = __name__
    for short, target in _ALIAS_MAP.items():
        alias = f"{pkg_name}.{short}"
        if alias in _sys.modules:
            continue
        try:
            _sys.modules[alias] = import_module(target)
        except Exception:
            # Best-effort: ignore missing optional modules
            pass


_register_aliases()

__all__ = []
