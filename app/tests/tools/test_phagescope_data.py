"""Tests for the self-contained PhageScope V2 data service, offer gating, and workspace materializer."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.routers.chat import action_execution, prompt_builder
from app.routers.chat.action_handlers import handle_tool_action
from app.routers.chat.agent import StructuredChatAgent
from app.routers.chat.request_routing import get_all_tools
from app.services import tool_schemas
from app.services.llm.structured_response import LLMAction
from app.services.phagescope_data_service import db_queries as dq
from app.services.phagescope_data_service import server as svc
from app.tests.tools.test_native_tool_schemas import (
    ALL_NATIVE_TOOLS,
    GOLDEN_PATH,
    _normalized,
    without_gated_tools,
)
from tool_box.context import ToolContext
from tool_box.tool_registry import register_all_tools
from tool_box.tools import get_tool_registry
from tool_box.tools_impl.execute_code import config as code_mode_config
from tool_box.tools_impl.phagescope_data import client as ps_client
from tool_box.tools_impl.phagescope_data import phagescope_data_handler

_TOOL = "phagescope_data"


@pytest.fixture(autouse=True)
def _cleanup_registry():
    yield
    reg = get_tool_registry()
    if reg.get_tool(_TOOL) is not None:
        reg.unregister_tool(_TOOL)
    tool_schemas._TOOL_REGISTRY_CACHE = None


@pytest.fixture()
def flag_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PHAGESCOPE_DATA_ENABLED", raising=False)
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


@pytest.fixture()
def flag_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "1")
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


# ----------------------------------------------------------------- offer gating


def test_flag_off_keeps_golden_master_and_hides_tool(flag_off: None) -> None:
    payload = {
        "build_tool_schemas_all20": without_gated_tools(
            tool_schemas.build_tool_schemas(ALL_NATIVE_TOOLS)
        ),
        "build_executor_tool_schemas": without_gated_tools(
            tool_schemas.build_executor_tool_schemas()
        ),
        "executor_available_tools": tool_schemas.EXECUTOR_AVAILABLE_TOOLS,
    }
    assert _normalized(payload) == GOLDEN_PATH.read_text(encoding="utf-8")
    assert _TOOL not in tool_schemas._get_tool_registry()
    assert _TOOL not in get_all_tools()
    assert _TOOL not in code_mode_config.allowed_tools()
    register_all_tools()
    assert get_tool_registry().get_tool(_TOOL) is None


def test_flag_on_exposes_tool_across_all_surfaces(flag_on: None) -> None:
    registry = tool_schemas._get_tool_registry()
    assert _TOOL in registry
    assert _TOOL in get_all_tools()
    assert _TOOL in code_mode_config.allowed_tools()

    native_names = [s["function"]["name"] for s in tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS))]
    exec_names = [s["function"]["name"] for s in tool_schemas.build_executor_tool_schemas()]
    assert _TOOL in native_names
    assert _TOOL in exec_names

    register_all_tools()
    tool_def = get_tool_registry().get_tool(_TOOL)
    assert tool_def is not None
    assert tool_def.category == "bioinformatics"

    agent_stub = SimpleNamespace(
        plan_session=SimpleNamespace(
            plan_id=None,
            outline=lambda **_: "[outline]",
            summaries_for_prompt=lambda **_: "[catalog]",
        ),
        extra_context={},
        history=[],
        mode="assistant",
        conversation_id="conv-ps",
        schema_json='{"llm_reply": {}, "actions": []}',
        _extract_tool_name=StructuredChatAgent._extract_tool_name,
    )
    catalog = prompt_builder.compose_action_catalog(agent_stub, plan_bound=False)
    assert "- tool_operation: phagescope_data (" in catalog


# --------------------------------------------------------- host read-only service


class _MockCursor:
    def __init__(self) -> None:
        self._rows: list[Any] = []

    def __enter__(self) -> _MockCursor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        _ = (exc_type, exc_val, exc_tb)

    def execute(self, sql: str, params: Any = None) -> None:
        s = " ".join(sql.split())
        if "SELECT 1" in s and "phage_protein_family" not in s:
            self._rows = [(1,)]
        elif "rolsuper" in s:
            self._rows = [(False,)]
        elif "SHOW default_transaction_read_only" in s:
            self._rows = [("on",)]
        elif "has_table_privilege" in s:
            self._rows = [(False,)]
        elif "FROM datasets" in s:
            self._rows = [(2, "RefSeq"), (27, "MetaVR")]
        elif "FROM phage_lifestyle WHERE phage_id = ANY" in s:
            self._rows = [(13, "temperate", "Predicted"), (99, "virulent", "Predicted")]
        elif "FROM phage_hosts WHERE phage_id = ANY" in s:
            self._rows = [
                (13, "Given", "Pseudomonadota", "Gammaproteobacteria", "Enterobacterales", "Enterobacteriaceae", "Escherichia", "Escherichia coli"),
                (99, "Predicted", "Pseudomonadota", "Gammaproteobacteria", "Pseudomonadales", "Pseudomonadaceae", "Pseudomonas", "Pseudomonas aeruginosa"),
            ]
        elif "GROUP BY" in s and "FROM phages p" in s:
            self._rows = [("Pseudomonas", 42, 38000, 62000, 285000), ("Escherichia", 19, 40000, 48502, 168000)]
        elif "SELECT COUNT(*) FROM phages p" in s:
            self._rows = [(2,)]
        elif "FROM phages p" in s:
            if params and "NC_001416" in str(params):
                if "LIKE" in s or "NC_001416.1" in str(params):
                    self._rows = [
                        (13, "NC_001416.1", 2, 48502, "49.86", "Escherichia coli", "Complete", "Drexlerviridae", "Cluster_1", "Subcluster_1", False, True)
                    ]
                else:
                    self._rows = []
            elif params and 99 in (params[0] if isinstance(params[0], list) else params):
                self._rows = [
                    (99, "MetaVR_jumbo_01", 27, 260000, "41.2", "Pseudomonas aeruginosa", "Complete", "Caudoviricetes", "Cluster_9", "Subcluster_9", False, True)
                ]
            else:
                self._rows = [
                    (13, "NC_001416.1", 2, 48502, "49.86", "Escherichia coli", "Complete", "Drexlerviridae", "Cluster_1", "Subcluster_1", False, True),
                    (99, "MetaVR_jumbo_01", 27, 260000, "41.2", "Pseudomonas aeruginosa", "Complete", "Caudoviricetes", "Cluster_9", "Subcluster_9", False, True),
                ]
        elif 'FROM "phage_protein_NCBI"' in s or 'FROM "phage_protein_MetaVR"' in s:
            if "COUNT(*)" in s:
                self._rows = [(2,)]
            else:
                self._rows = [
                    ("MetaVR_jumbo_01_1", "MetaVR_jumbo_01", 100, 940, "+", "endolysin", "lysis", "MetaVR", "MKVLL" * 50),
                    ("MetaVR_jumbo_01_2", "MetaVR_jumbo_01", 960, 1500, "+", "holin", "lysis", "MetaVR", "MGLAA" * 30),
                ]
        elif "FROM phage_protein_family_member WHERE protein_id = ANY" in s:
            self._rows = [("MetaVR_jumbo_01_1", "MetaVR")]
        elif "FROM phage_protein_family WHERE" in s:
            self._rows = [
                ("PFAM_LYSIN_001", 1280, 940, "lysis", "peptidoglycan endolysin", 14, "MetaVR_jumbo_01_1", 18)
            ]
        elif "SELECT 1 FROM phage_protein_family" in s:
            self._rows = [(1,)]
        elif "FROM phage_protein_family_member WHERE" in s:
            if "COUNT(*)" in s:
                self._rows = [(1,)]
            else:
                self._rows = [
                    ("MetaVR_jumbo_01_1", "PFAM_LYSIN_001", "SUB_01", "MetaVR", "MetaVR_jumbo_01", "endolysin", "lysis")
                ]
        elif "FROM phage_antimicrobial_resistance_gene" in s:
            if "COUNT(*)" in s:
                self._rows = [(1,)]
            else:
                self._rows = [("GPD_10_4", "NDM-1 beta-lactamase", "uvig_10", "GPD")]
        elif "FROM protein_structure_map m" in s:
            self._rows = [
                (
                    "MetaVR_jumbo_01_1",
                    "ESMFOLD:MetaVR_jumbo_01_1",
                    "High",
                    "exact",
                    1.0,
                    1.0,
                    1.0,
                    "ESMFold",
                    "ESMFold",
                    88.4,
                    None,
                    "pdb",
                    "/data4/back_up_platform/esmfold_pdb/5/split_1/MetaVR_jumbo_01_1.pdb",
                )
            ]
        else:
            self._rows = []

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _MockConn:
    def cursor(self) -> _MockCursor:
        return _MockCursor()


def test_readonly_role_gate_enforces_safety() -> None:
    assert dq.verify_readonly_connection(_MockConn()) is None

    class _SuperCursor(_MockCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            self._rows = [(True,)] if "rolsuper" in sql else [("on",)]

    class _SuperConn:
        def cursor(self) -> _MockCursor:
            return _SuperCursor()

    assert dq.verify_readonly_connection(_SuperConn()) == "database role is a superuser"


@pytest.fixture()
def mock_phagescope_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag_on: None) -> Path:
    # Build mock read-only PhageScope catalog tree
    seq_dir = tmp_path / "phage_sequence"
    refseq_fa = seq_dir / "phage_fasta" / "RefSeq"
    metavr_fa = seq_dir / "phage_fasta" / "MetaVR"
    refseq_gff = seq_dir / "phage_gff3" / "individual_data" / "RefSeq"
    refseq_fa.mkdir(parents=True)
    metavr_fa.mkdir(parents=True)
    refseq_gff.mkdir(parents=True)

    _ = (refseq_fa / "NC_001416.1.fasta").write_text(">NC_001416.1 Enterobacteria phage lambda\n" + ("ACGT" * 2000) + "\n")
    # Jumbo phage >250 kb — proves no 200,000 bp truncation when sequence mount is present!
    _ = (metavr_fa / "MetaVR_jumbo_01.fasta").write_text(">MetaVR_jumbo_01 Jumbo phage\n" + ("ACGT" * 65000) + "\n")
    _ = (refseq_gff / "NC_001416.1.gff3").write_text("##gff-version 3\nNC_001416.1\tProdigal\tCDS\t191\t736\t.\t+\t0\tID=1\n")

    csv_dir = tmp_path / "csv_download"
    (csv_dir / "Phage_meta_data").mkdir(parents=True)
    term_dir = csv_dir / "Transcription_terminators_meta_data"
    term_dir.mkdir(parents=True)
    _ = (term_dir / "refseq_terminators.tsv").write_text(
        "Phage_ID\tTerminator_Id\tStart\tStop\tSense\tConfidence\nNC_001416.1\tTERM_1\t48100\t48140\t+\t98\n"
    )

    pdb_dir = tmp_path / "esmfold_pdb"
    pdb_sub = pdb_dir / "5" / "split_1"
    pdb_sub.mkdir(parents=True)
    _ = (pdb_sub / "MetaVR_jumbo_01_1.pdb").write_text("HEADER    MOCK ESMFOLD ENDOLYSIN\nATOM      1  CA  MET A   1\nEND\n")

    @contextlib.contextmanager
    def _conn_ctx() -> Iterator[_MockConn]:
        yield _MockConn()

    overview = json.loads(svc.OVERVIEW_PATH.read_text(encoding="utf-8"))
    state = svc.DataServiceState(
        api_key="secret-ro-key",
        conn_provider=_conn_ctx,
        overview=overview,
        fasta_root=seq_dir / "phage_fasta",
        csv_download_root=csv_dir,
    )

    async def _fake_call_service(
        path: str,
        *,
        method: str = "GET",
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        _ = timeout
        url = f"/agent-api/v1/data/{path.lstrip('/')}"
        if query:
            from urllib.parse import urlencode
            url += "?" + urlencode(query)
        raw_body = json.dumps(body).encode("utf-8") if body is not None else b""
        status, resp = svc.dispatch_request(
            state,
            method,
            url,
            {"X-API-Key": "secret-ro-key", "Host": "127.0.0.1:18326"},
            raw_body,
        )
        if status >= 400:
            err = resp.get("error", {})
            from tool_box.tools_impl.phagescope_data.config import PhageScopeDataError
            raise PhageScopeDataError(err.get("code", "err"), err.get("message", "failed"), status)
        return resp

    monkeypatch.setattr(ps_client, "call_service", _fake_call_service)
    monkeypatch.setenv("PHAGESCOPE_DATA_SERVICE_URL", "http://127.0.0.1:18326")
    monkeypatch.setenv("PHAGESCOPE_DATA_SERVICE_KEY", "secret-ro-key")
    monkeypatch.setenv("PHAGESCOPE_SEQ_DIR", str(seq_dir))
    monkeypatch.setenv("PHAGESCOPE_CSV_DOWNLOAD_DIR", str(csv_dir))
    monkeypatch.setenv("PHAGESCOPE_PDB_DIR", str(pdb_dir))
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    return tmp_path


def test_data_service_rejects_bad_api_key() -> None:
    @contextlib.contextmanager
    def _conn_ctx() -> Iterator[_MockConn]:
        yield _MockConn()

    state = svc.DataServiceState(api_key="real-key", conn_provider=_conn_ctx, overview={})
    status, body = svc.dispatch_request(state, "GET", "/agent-api/v1/data/health/", {"X-API-Key": "wrong"})
    assert status == 401
    assert body["error"]["code"] == "unauthorized"
    # urllib/httpx may send mixed-case 'X-api-key'
    ok_status, ok_body = svc.dispatch_request(state, "GET", "/agent-api/v1/data/health/", {"X-api-key": "real-key"})
    assert ok_status == 200 and ok_body == {"status": "ok"}


async def test_overview_search_aggregate_and_features(mock_phagescope_env: Path) -> None:
    work_dir = mock_phagescope_env / "runtime" / "session_ps"
    work_dir.mkdir(parents=True)
    ctx = ToolContext(session_id="session_ps", work_dir=str(work_dir))

    ov = await phagescope_data_handler(operation="overview", tool_context=ctx)
    assert ov["success"] is True
    assert ov["overview"]["phages_total"] == 2576926
    assert ov["mounts"]["sequence_mount_ready"] is True

    # Versionless accession fallback: NC_001416 -> NC_001416.1
    one = await phagescope_data_handler(operation="get_phage", accession="NC_001416", tool_context=ctx)
    assert one["success"] is True
    assert one["phage"]["Acession_ID"] == "NC_001416.1"
    assert one["phage"]["host_lineage"]["Genus"] == "Escherichia"

    # Cohort search + save_tsv
    cohort = await phagescope_data_handler(
        operation="search_phages",
        host_rank="Genus",
        host_name="Pseudomonas",
        completeness=["Complete", "High-quality"],
        save_tsv=True,
        output_subdir="pseudo_cohort",
        tool_context=ctx,
    )
    assert cohort["success"] is True
    assert cohort["saved_rows"] == 2
    assert any("phages_cohort.tsv" in p for p in cohort["produced_files"])

    # Aggregate distribution
    agg = await phagescope_data_handler(operation="aggregate", group_by="host_genus", dataset="MetaVR")
    assert agg["success"] is True
    assert agg["groups"][0]["key"] == "Pseudomonas"

    # Features: fast on-disk terminator TSV scan + CARD AMR search
    term = await phagescope_data_handler(operation="features", feature_type="terminator", accession="NC_001416.1")
    assert term["success"] is True
    assert term["results"][0]["Terminator_Id"] == "TERM_1"

    amr = await phagescope_data_handler(operation="features", feature_type="amr", keyword="NDM-1")
    assert amr["success"] is True
    assert amr["results"][0]["Aligned_Protein_in_CARD"] == "NDM-1 beta-lactamase"


async def test_untruncated_jumbo_phage_and_metavr_faa_and_pdb_materialization(
    mock_phagescope_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = mock_phagescope_env / "runtime" / "session_mat"
    work_dir.mkdir(parents=True)
    ctx = ToolContext(session_id="session_mat", work_dir=str(work_dir))

    # Materialize 260 kb jumbo phage from MetaVR (which has no pre-dumped phage_faa file,
    # proving dynamic full-sequence .faa assembly from PostgreSQL + untruncated 260 kb FASTA!)
    mat = await phagescope_data_handler(
        operation="materialize",
        phage_ids=[99],
        kinds=["fasta", "faa", "metadata_tsv"],
        output_subdir="jumbo_metavr",
        tool_context=ctx,
    )
    assert mat["success"] is True
    assert mat["truncated_items"] == []
    bundle_dir = Path(mat["output_directory"])
    fasta_content = (bundle_dir / "genomes.fasta").read_text()
    assert len(fasta_content) > 250_000  # Not truncated at 200,000 bp!
    faa_content = (bundle_dir / "proteins.faa").read_text()
    assert ">MetaVR_jumbo_01_1 endolysin" in faa_content

    # Materialize protein family + 3D .pdb structure
    prot_mat = await phagescope_data_handler(
        operation="materialize",
        family_id="PFAM_LYSIN_001",
        kinds=["faa", "pdb"],
        output_subdir="lysin_family",
        tool_context=ctx,
    )
    assert prot_mat["success"] is True
    assert prot_mat["pdb_copied"] == 1
    assert (Path(prot_mat["output_directory"]) / "structures" / "MetaVR_jumbo_01_1.pdb").is_file()

    # Chat dispatcher + result compaction + tool analysis integration
    register_all_tools()
    agent_stub = SimpleNamespace(
        session_id="session_mat",
        conversation_id="conv_mat",
        extra_context={},
        plan_session=SimpleNamespace(plan_id=None, repo=None),
        _sync_task_status_after_tool_execution=lambda **_: None,
    )
    action = LLMAction(
        kind="tool_operation",
        name="phagescope_data",
        parameters={"operation": "search_proteins", "family_query": "endolysin"},
        order=1,
    )
    step = await handle_tool_action(agent_stub, action)
    assert step.success is True
    assert step.details["result"]["results"][0]["family_id"] == "PFAM_LYSIN_001"

    captured: list[str] = []

    class _FakeLLM:
        async def chat_async(self, prompt: str) -> str:
            captured.append(prompt)
            return "Identified family PFAM_LYSIN_001 (1280 members)."

    monkeypatch.setattr(action_execution, "_get_llm_service_for_provider", lambda _: _FakeLLM())
    analysis = await action_execution._generate_tool_analysis(
        user_message="Find endolysin protein families in PhageScope",
        tool_results=[
            {
                "name": "phagescope_data",
                "summary": step.details.get("summary"),
                "result": step.details.get("result"),
            }
        ],
    )
    assert analysis == "Identified family PFAM_LYSIN_001 (1280 members)."
    assert "PFAM_LYSIN_001" in captured[0]
