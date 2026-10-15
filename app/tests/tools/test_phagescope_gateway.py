"""Gateway contract, HTTP, submission continuity and task-scoped file delivery."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tool_box.context import ToolContext
from tool_box.phagescope_gateway_contract import MODULES, ROUTES, gateway_schema
from tool_box.tools_impl import phagescope_gateway_client as client
from tool_box.tools_impl.phagescope import phagescope_handler, phagescope_tool


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    settings = SimpleNamespace(phagescope_gateway_base_url="https://gateway.test",
        phagescope_gateway_data_url=None, phagescope_gateway_analysis_url=None,
        phagescope_gateway_data_key="test-data-key", phagescope_gateway_analysis_key="test-analysis-key")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from app.services.session_paths import get_runtime_session_dir
    root = get_runtime_session_dir("gateway-test", create=True)
    task = root / "raw_files/task_1/task_2"; task.mkdir(parents=True)
    context = ToolContext(session_id="gateway-test", task_id=2, work_dir=str(task))
    calls = []
    real_client = httpx.AsyncClient

    def install(handler):
        def dispatch(request):
            calls.append(request)
            return handler(request)
        transport = httpx.MockTransport(dispatch)
        monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))

    async def call(operation, params=None, **kwargs):
        return await phagescope_handler(action="gateway", gateway_action=operation,
            gateway_params=params, tool_context=context, **kwargs)
    return SimpleNamespace(settings=settings, context=context, root=root, task=task, calls=calls, install=install, call=call)


CASES = [
    ("data_health", {}, "data/health/"),
    ("analysis_health", {}, "analysis/health/"),
    ("overview", {}, "data/stats/overview/"),
    ("datasets", {}, "data/datasets/"),
    ("phages", {"host":"Klebsiella", "page":2, "pagesize":50}, "data/phages/"),
    ("phage", {"phage_id":5}, "data/phages/5/"),
    ("phage_proteins", {"phage_id":5,"include_sequence":True}, "data/phages/5/proteins/"),
    ("phage_features", {"phage_id":5,"type":"amr"}, "data/phages/5/features/"),
    ("phage_sequence", {"phage_id":5,"max_bases":200000}, "data/phages/5/sequence/"),
    ("protein", {"protein_id":"NC_001_1"}, "data/proteins/NC_001_1/"),
    ("hosts", {"rank":"Genus","name":"Klebsiella"}, "data/hosts/"),
    ("families", {"protein_id":"NC_001_1"}, "data/families/"),
    ("family", {"family_id":"F_1"}, "data/families/F_1/"),
    ("family_members", {"family_id":"F_1","dataset":"Genbank"}, "data/families/F_1/members/"),
    ("structures", {"protein_ids":["p1","p2"]}, "data/structures/available/"),
    ("status", {"job_id":7}, "analysis/jobs/7/"),
    ("results", {"job_id":7,"module":"quality"}, "analysis/jobs/7/results/"),
]


@pytest.mark.parametrize("operation,params,path", CASES)
async def test_every_read_endpoint_uses_its_channel_key(gateway, operation, params, path):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/agent-api/v1/" + path
        key = "test-analysis-key" if path.startswith("analysis") else "test-data-key"
        assert request.headers["X-API-Key"] == key
        assert "Authorization" not in request.headers
        return httpx.Response(200,json={"status":"ok","results":[{"value":2}]})
    gateway.install(handler)
    result = await gateway.call(operation, params)
    assert result["success"] and result["data"]["results"] == [{"value":2}]
    if operation == "phage_proteins":
        assert gateway.calls[0].url.params["include_sequence"] == "1"
    if operation == "structures":
        assert gateway.calls[0].url.params["protein_ids"] == "p1,p2"


async def test_capabilities_are_local_and_do_not_claim_remote_reachability(gateway):
    gateway.install(lambda r: pytest.fail("Capabilities must not make a request"))
    result = await gateway.call("capabilities")
    assert result["channels"]["data"]["ready"]
    assert set(result["analysis_modules"]) == set(MODULES)
    assert len(ROUTES) == 17
    assert result["content_review_required"] is False
    assert not result["full_file_download_supported"] and not result["remote_cancel_supported"]
    assert "data_health" in result["operations"]


@pytest.mark.parametrize("operation,params", [
    ("phages",{}), ("phage",{"phage_id":True}), ("status",{"job_id":0}),
    ("phage_features",{"phage_id":5}), ("hosts",{}), ("families",{}),
    ("structures",{}), ("structures",{"protein_id":"p1","protein_ids":["p2"]}),
    ("structures",{"protein_ids":["p"]*21}), ("phages",{"host":"K","sql":"select"}),
    ("phages",{"host":"K","gc_min":float("nan")}),
    ("submit",{"modules":["unknown"],"fasta":">x\nACGT\n"}),
    ("submit",{"modules":["quality"],"fasta":">x\nACGT\n","accession":"NC_1"}),
    ("submit",{"modules":["quality"],"fasta":">x\nprotein\n"}),
    ("submit",{"modules":["quality"],"fasta":">empty\n"}),
])
async def test_invalid_requests_never_reach_gateway(gateway, operation, params):
    gateway.install(lambda r: pytest.fail("Invalid input reached HTTP"))
    result = await gateway.call(operation, params)
    assert not result["success"] and result["status_code"] == 400


async def test_missing_configuration_does_not_fall_back_to_website(gateway, monkeypatch):
    gateway.settings.phagescope_gateway_data_key = None
    gateway.install(lambda r: pytest.fail("No request without configuration"))
    result = await gateway.call("datasets")
    assert result["error_code"] == "configuration_required"
    assert "test-analysis-key" not in json.dumps(result)


async def test_process_origins_and_existing_api_roots(gateway):
    gateway.settings.phagescope_gateway_data_url = "http://data.test:8001/agent-api/v1/data/"
    gateway.settings.phagescope_gateway_analysis_url = "http://analysis.test:8002"
    gateway.install(lambda r: httpx.Response(200,json={"status":"ok"}))
    await gateway.call("data_health"); await gateway.call("analysis_health")
    assert str(gateway.calls[0].url) == "http://data.test:8001/agent-api/v1/data/health/"
    assert str(gateway.calls[1].url) == "http://analysis.test:8002/agent-api/v1/analysis/health/"


@pytest.mark.parametrize("module", MODULES)
async def test_all_analysis_modules_can_be_submitted_once(gateway, module):
    gateway.install(lambda r: httpx.Response(201,json={"job_id":7,"status":"running"}))
    result = await gateway.call("submit",{"modules":[module,module],"accession":"NC_001609.1"})
    assert result["success"] and result["job_id"] == 7 and len(gateway.calls) == 1
    body = json.loads(gateway.calls[0].content)
    assert body["modules"].count(module) == 1
    assert set(body) == {"modules","accession"}
    assert gateway.calls[0].method == "POST"
    assert gateway.calls[0].headers["X-API-Key"] == "test-analysis-key"
    if module in {"taxonomic","alignment","anticrispr","arvf","transmembrane","terminator","cluster"}:
        assert body["modules"] == ["annotation",module]
    receipt = json.loads(Path(result["artifact_paths"][0]).read_text())
    assert receipt["job_id"] == 7 and receipt["state"] == "running"


async def test_submit_reads_upstream_fasta_and_returns_a_durable_receipt(gateway):
    upstream = gateway.root/"raw_files/task_1/task_3/input.fasta"
    upstream.parent.mkdir();upstream.write_text(">x\nACGT\n")
    gateway.install(lambda r: httpx.Response(201,json={"job_id":9,"status":"running"}))
    result = await gateway.call("submit",{"modules":["quality"],"file_path":str(upstream)})
    assert json.loads(gateway.calls[0].content)["fasta"] == ">x\nACGT\n"
    assert result["output_location"]["task_id"] == 2
    assert all(Path(p).is_relative_to(gateway.task) for p in result["artifact_paths"])
    assert all("test-analysis-key" not in Path(p).read_text() for p in result["artifact_paths"])


async def test_submission_timeout_is_unknown_and_never_retried(gateway):
    def fail(request):
        raise httpx.ReadTimeout("fixture timeout",request=request)
    gateway.install(fail)
    result = await gateway.call("submit",{"modules":["quality"],"fasta":">x\nACGT\n"})
    assert result["submission_unknown"] and len(gateway.calls) == 1
    assert result["error_code"] == "submission_unknown"
    assert json.loads(Path(result["artifact_paths"][0]).read_text())["state"] == "submission_unknown"


async def test_execution_failure_is_preserved_with_the_job_id(gateway):
    gateway.install(lambda r: httpx.Response(201,json={"job_id":7,"status":"failed"}))
    result = await gateway.call("submit",{"modules":["quality"],"accession":"NC_1"})
    assert not result["success"] and result["job_id"] == 7
    assert result["execution_status"] == "failed"
    assert result["error_code"] == "remote_execution_failed"


async def test_receipt_disk_error_cannot_lose_an_accepted_job_id(gateway, monkeypatch):
    from tool_box.tools_impl.phagescope_gateway_artifacts import Output
    real_json=Output.json
    writes=[]
    def disk_full(self,name,value):
        writes.append(name)
        if len(writes)>1:
            raise OSError("fixture disk full")
        return real_json(self,name,value)
    monkeypatch.setattr(Output,"json",disk_full)
    gateway.install(lambda r: httpx.Response(201,json={"job_id":7,"status":"running"}))
    result=await gateway.call("submit",{"modules":["quality"],"accession":"NC_1"})
    assert result["success"] and result["job_id"]==7
    assert result["receipt_write_error"]=="OSError" and len(gateway.calls)==1


async def test_malformed_accepted_submission_is_not_recorded_as_rejected(gateway):
    gateway.install(lambda r: httpx.Response(201,json={"status":"running"}))
    result=await gateway.call("submit",{"modules":["quality"],"accession":"NC_1"})
    assert result["submission_unknown"]
    assert json.loads(Path(result["artifact_paths"][0]).read_text())["state"]=="submission_unknown"


async def test_rate_limit_and_invalid_json_are_explicit(gateway):
    gateway.install(lambda r: httpx.Response(429,json={"error":{"code":"too_many_requests","message":"busy"}},headers={"Retry-After":"20"}))
    result = await gateway.call("data_health")
    assert not result["success"] and result["retry_after"] == "20" and len(gateway.calls) == 1
    gateway.install(lambda r: httpx.Response(200,text="<html>login</html>"))
    result = await gateway.call("data_health")
    assert result["error_code"] == "invalid_gateway_response"


async def test_data_and_analysis_saved_files_are_real_without_content_review(gateway):
    def handler(request):
        if request.url.path.endswith("/jobs/7/"):
            return httpx.Response(200,json={"job_id":7,"status":"completed","modules":[{"module":"quality","state":"COMPLETED"},{"module":"annotation","state":"COMPLETED"}]})
        if request.url.params["module"] == "quality":
            files={"quality_summary.tsv":{"present":True,"truncated":False,"text":"id\tquality\nx\thigh\n"},"checkv_result.txt":{"present":False}}
        else:
            files={"gene.faa":{"present":True,"truncated":True,"text":">p\nMSEQ"}}
        return httpx.Response(200,json={"module":request.url.params["module"],"files":files})
    gateway.install(handler)
    result = await gateway.call("save_results",{"job_id":7})
    assert result["success"] and result["execution_status"] == "completed"
    assert "annotation/gene.faa" in result["truncated_files"]
    assert "quality/checkv_result.txt" in result["missing_files"]
    paths = [Path(p) for p in result["artifact_paths"]]
    assert any(p.name == "gene.faa.preview" for p in paths)
    assert not any(p.name == "gene.faa" for p in paths)
    manifest = json.loads(next(p for p in paths if p.name=="manifest.json").read_text())
    assert manifest["content_review"] == "not_requested"
    assert manifest["full_file_download_supported"] is False
    for row in manifest["files"]:
        p=Path(result["output_location"]["base_dir"])/row["path"]
        assert p.stat().st_size == row["bytes"]
        assert hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]


async def test_partial_collection_keeps_successful_files(gateway):
    def handler(request):
        if request.url.path.endswith("/jobs/7/"):
            return httpx.Response(200,json={"status":"failed","modules":[{"module":"quality"},{"module":"host"}]})
        if request.url.params["module"] == "host":
            return httpx.Response(503,json={"error":{"code":"unavailable","message":"busy"}})
        return httpx.Response(200,json={"files":{"quality_summary.tsv":{"present":True,"text":"ok"}}})
    gateway.install(handler)
    result = await gateway.call("save_results",{"job_id":7})
    assert not result["success"] and result["status_code"] == 207
    assert result["execution_status"] == "failed"
    assert any(Path(p).name=="quality_summary.tsv" for p in result["artifact_paths"])


async def test_query_save_preserves_sequence_preview_scope(gateway):
    gateway.install(lambda r: httpx.Response(200,json={"header":">x","sequence":"ACGT","truncated":True}))
    result = await gateway.call("phage_sequence",{"phage_id":5,"save":True})
    assert result["success"] and result["truncated_files"] == ["sequence.preview.fasta"]
    assert any(Path(p).name=="sequence.preview.fasta" for p in result["artifact_paths"])


async def test_directory_binary_text_is_saved_as_preview_without_quality_gate(gateway):
    def handler(request):
        if request.url.path.endswith("/jobs/7/"):
            return httpx.Response(200,json={"status":"completed","modules":[{"module":"cluster"}]})
        return httpx.Response(200,json={"module":"cluster","files":{
            "plot.png":{"present":True,"truncated":False,"text":"\ufffdPNG\r\n\x00fixture"},
            "data.zip":{"present":True,"truncated":True,"text":"PK\x00fixture"},
            "clusters.tsv":{"present":True,"truncated":False,"text":"id\tcluster\np1\t1\n"}}})
    gateway.install(handler)
    result=await gateway.call("save_results",{"job_id":7})
    assert result["success"] and result["execution_status"]=="completed"
    assert result["text_only_files"]==["cluster/plot.png","cluster/data.zip"]
    assert result["truncated_files"]==["cluster/data.zip"]
    names={Path(p).name for p in result["artifact_paths"]}
    assert {"plot.png.text-preview.txt","data.zip.text-preview.txt","clusters.tsv"}<=names
    assert not {"plot.png","data.zip"}&names
    manifest=json.loads(next(Path(p) for p in result["artifact_paths"] if Path(p).name=="manifest.json").read_text())
    assert manifest["text_only_files"]==result["text_only_files"]
    assert manifest["content_review"]=="not_requested"


async def test_collection_uses_one_deadline_and_keeps_earlier_files(gateway):
    async def handler(request):
        if request.url.path.endswith("/jobs/7/"):
            return httpx.Response(200,json={"status":"completed","modules":[{"module":"quality"},{"module":"host"}]})
        if request.url.params["module"]=="host":
            await asyncio.sleep(1)
        return httpx.Response(200,json={"files":{"quality_summary.tsv":{"present":True,"text":"ok"}}})
    gateway.install(handler)
    result=await gateway.call("save_results",{"job_id":7},timeout=.1)
    assert not result["success"] and result["status_code"]==207
    assert any(Path(p).name=="quality_summary.tsv" for p in result["artifact_paths"])


async def test_cancel_before_request_never_submits(gateway):
    gateway.context.abort_event=asyncio.Event();gateway.context.abort_event.set()
    gateway.install(lambda r: pytest.fail("Cancelled request reached gateway"))
    result=await gateway.call("submit",{"modules":["quality"],"accession":"NC_1"})
    assert result["error_code"]=="cancelled"


def test_gateway_schema_and_chat_receipts_survive_all_surfaces():
    from app.services.tool_schemas import build_tool_schemas
    from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result
    native=build_tool_schemas(["phagescope"])[0]["function"]["parameters"]["properties"]
    strict=phagescope_tool["parameters_schema"]["properties"]
    for key,value in gateway_schema().items():
        assert native[key]==strict[key]==value
    raw={"success":True,"action":"gateway","gateway_action":"submit","job_id":7,
         "execution_status":"running","data":{"job_id":7,"status":"running"},
         "artifact_paths":["/task/receipt.json"],"produced_files":["/task/receipt.json"]}
    compact=sanitize_tool_result("phagescope",raw)
    assert compact["job_id"]==7 and compact["artifact_paths"]==raw["artifact_paths"]
    assert "job #7" in summarize_tool_result("phagescope",compact)
    large=sanitize_tool_result("phagescope",{**raw,"data":{"files":{"x":{"text":"A"*10000,"truncated":False}}}})
    assert large["display_truncated"] and large["data"]["files"]["x"]["truncated"] is False


async def test_gateway_through_real_tool_executor_and_registry(gateway):
    import tool_box
    from app.services.execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor
    await tool_box.initialize_toolbox()
    gateway.install(lambda r: httpx.Response(201,json={"job_id":77,"status":"running"}))
    executor=UnifiedToolExecutor()
    result=await executor.execute("phagescope",{
        "action":"gateway","gateway_action":"submit",
        "gateway_params":{"modules":["quality"],"fasta":">x\nACGT\n"}},
        context=ToolExecutionContext(session_id=gateway.context.session_id,task_id=2,work_dir=str(gateway.task)))
    assert result["success"] and result["result"]["job_id"]==77
    assert "job #77" in result["summary"]
    assert all(Path(p).is_relative_to(gateway.task) for p in result["result"]["artifact_paths"])
