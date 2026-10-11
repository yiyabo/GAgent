"""
Tool Implementations

This module contains concrete implementations of various tools
that can be used by AI agents.
"""

from .file_operations import file_operations_tool
from .graph_rag import graph_rag_tool
from .lightrag_query import lightrag_query_tool
from .web_search import web_search_tool
from .code_executor import code_executor_tool
from .document_reader import document_reader_tool
from .vision_reader import vision_reader_tool
from .paper_replication import paper_replication_tool
from .generate_experiment_card import generate_experiment_card_tool
from .manuscript_writer import manuscript_writer_tool
from .literature_pipeline import literature_pipeline_tool
from .review_pack_writer import review_pack_writer_tool
from .phagescope import phagescope_tool
from .phagescope_research import phagescope_research_tool
from .result_interpreter import result_interpreter_tool
from .plan_tools import plan_operation_tool
from .sequence_fetch import sequence_fetch_tool
from .url_fetch import url_fetch_tool
from .scientific_figure_generator import scientific_figure_generator_tool
from .terminal_session import terminal_session_tool
from .execute_code import execute_code_tool
from .delegate_task import delegate_task_tool
from .load_skill import load_skill_tool


def _rebind_cached_submodules() -> None:
    """Re-attach every already-loaded submodule to this package object.

    ``from .x import y`` binds ``y``; it never binds the *submodule* ``x``.  A
    submodule attribute is set by the import system only at the moment that
    submodule is first loaded, and that moment is not necessarily this
    module's own init: ``app.services.terminal.session_manager`` imports
    ``tool_box.bio_tools.remote_executor`` at module scope, which pulls in
    ``tool_box`` -> ``integration`` -> ``tool_registry`` -> ``.tools_impl``
    while ``session_manager`` itself is only half-initialized.  The
    ``from .terminal_session import ...`` line above then reaches back into
    that partial module and raises ImportError; CPython removes
    ``tool_box.tools_impl`` from ``sys.modules`` but keeps the submodules it
    had already loaded.  A later ``import tool_box.tools_impl`` builds a fresh
    package object, and for each cached submodule the import lines above bind
    only the ``*_tool`` object -- so ``tool_box.tools_impl.plan_tools`` and
    friends come out missing, and every test patching a string target such as
    ``tool_box.tools_impl.plan_tools.resolve_plan_review_result`` dies with
    AttributeError (20 tests, measured 2026-10-11).

    Rebinding from ``sys.modules`` makes the package carry the same attributes
    in either import order, without changing what the lines above import.
    """
    import sys

    package = sys.modules[__name__]
    prefix = __name__ + "."
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith(prefix):
            continue
        child = module_name[len(prefix) :]
        if "." in child:
            # Nested submodule (e.g. ``web_search.handler``); its own parent binds it.
            continue
        setattr(package, child, module)


_rebind_cached_submodules()


__all__ = [
    "web_search_tool",
    "file_operations_tool",
    "graph_rag_tool",
    "lightrag_query_tool",
    "code_executor_tool",
    "document_reader_tool",
    "vision_reader_tool",
    "paper_replication_tool",
    "generate_experiment_card_tool",
    "manuscript_writer_tool",
    "literature_pipeline_tool",
    "review_pack_writer_tool",
    "phagescope_tool",
    "phagescope_research_tool",
    "result_interpreter_tool",
    "plan_operation_tool",
    "sequence_fetch_tool",
    "url_fetch_tool",
    "scientific_figure_generator_tool",
    "terminal_session_tool",
    "execute_code_tool",
    "delegate_task_tool",
    "load_skill_tool",
]
