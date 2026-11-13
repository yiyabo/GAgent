"""Self-contained read-only HTTP server for PhageScope V2 catalog & research queries.

Runs on the 620b host under `/home/platform/.conda/envs/phage/bin/python` (which
provides `psycopg2`) and binds `127.0.0.1:18326` only. Never touches PhageScope's
website code or tables; enforces `default_transaction_read_only=on` and
`statement_timeout=10000` on every PostgreSQL connection and verifies at startup
that the role cannot INSERT/UPDATE/DELETE `phages`.
"""

from __future__ import annotations

import contextlib
import hmac
import importlib
import json
import logging
import os
import re
import sys
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

try:
    from . import db_queries as dq
except ImportError:  # pragma: no cover - direct script execution on host
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    dq = importlib.import_module("db_queries")

logger = logging.getLogger("phagescope_data_service")

MAX_BODY_BYTES = 1024 * 1024
OVERVIEW_PATH = Path(__file__).resolve().with_name("overview.json")

_RE_PHAGE_ID = re.compile(r"^/agent-api/v1/data/phages/(\d+)/?$")
_RE_PHAGE_SUB = re.compile(r"^/agent-api/v1/data/phages/(\d+)/(proteins|features|sequence)/?$")
_RE_PROTEIN_ID = re.compile(r"^/agent-api/v1/data/proteins/([^/]+)/?$")
_RE_FAMILY_ID = re.compile(r"^/agent-api/v1/data/families/([^/]+)/?$")
_RE_FAMILY_MEMBERS = re.compile(r"^/agent-api/v1/data/families/([^/]+)/members/?$")


class DataServiceState:
    def __init__(
        self,
        *,
        api_key: str,
        conn_provider: Callable[[], contextlib.AbstractContextManager[Any]],
        overview: dict[str, Any],
        fasta_root: Path | None = None,
        fasta_ext_root: Path | None = None,
        csv_download_root: Path | None = None,
        pdb_host_prefix: str = "/data4/back_up_platform/esmfold_pdb/",
    ) -> None:
        self.api_key = api_key
        self.conn_provider = conn_provider
        self.overview = overview
        self.fasta_root = fasta_root
        self.fasta_ext_root = fasta_ext_root
        self.csv_download_root = csv_download_root
        self.pdb_host_prefix = pdb_host_prefix


def _page_links(
    base_url: str,
    path: str,
    query: dict[str, str],
    page: int,
    pagesize: int,
    total: int,
) -> tuple[str | None, str | None]:
    nxt = None
    prev = None
    if page * pagesize < total:
        q = {**query, "page": str(page + 1), "pagesize": str(pagesize)}
        nxt = f"{base_url}{path}?{urlencode(q)}"
    if page > 1:
        q = {**query, "page": str(page - 1), "pagesize": str(pagesize)}
        prev = f"{base_url}{path}?{urlencode(q)}"
    return nxt, prev


def dispatch_request(
    state: DataServiceState,
    method: str,
    raw_url: str,
    headers: dict[str, str],
    body_bytes: bytes = b"",
) -> tuple[int, dict[str, Any]]:
    lower_headers = {str(k).lower(): str(v) for k, v in headers.items()}
    presented = lower_headers.get("x-api-key", "")
    if not state.api_key or not presented or not hmac.compare_digest(
        presented.encode("utf-8"), state.api_key.encode("utf-8")
    ):
        return 401, {"error": {"code": "unauthorized", "message": "A valid X-API-Key header is required."}}

    parsed = urlsplit(raw_url)
    path = parsed.path
    qs_multi = parse_qs(parsed.query, keep_blank_values=False)
    qs = {k: v[-1] for k, v in qs_multi.items() if v}
    host_hdr = lower_headers.get("host") or "127.0.0.1:18326"
    base_url = f"http://{host_hdr}"

    try:
        if method == "GET":
            return _handle_get(state, path, qs, base_url)
        if method == "POST":
            if len(body_bytes) > MAX_BODY_BYTES:
                return 413, {"error": {"code": "payload_too_large", "message": "Request body exceeds 1 MiB."}}
            try:
                payload = json.loads(body_bytes.decode("utf-8") or "{}") if body_bytes else {}
            except ValueError:
                return 400, {"error": {"code": "invalid_parameter", "message": "Body must be valid JSON."}}
            if not isinstance(payload, dict):
                return 400, {"error": {"code": "invalid_parameter", "message": "Body must be a JSON object."}}
            return _handle_post(state, path, payload)
        return 405, {"error": {"code": "method_not_allowed", "message": "Only GET and POST are supported."}}
    except dq.ServiceQueryError as exc:
        return exc.status, {"error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unhandled query error on %s %s", method, path)
        return 503, {"error": {"code": "unavailable", "message": f"Database query failed: {type(exc).__name__}"}}


def _int_param(qs: dict[str, Any], name: str, default: int) -> int:
    if name not in qs or qs[name] is None or qs[name] == "":
        return default
    try:
        return int(qs[name])
    except (TypeError, ValueError) as exc:
        raise dq.ServiceQueryError("invalid_parameter", f"{name} must be an integer.") from exc


def _handle_get(
    state: DataServiceState,
    path: str,
    qs: dict[str, str],
    base_url: str,
) -> tuple[int, dict[str, Any]]:
    norm = path.rstrip("/") + "/"
    if norm == "/agent-api/v1/data/health/":
        with state.conn_provider() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return 200, {"status": "ok"}

    if norm == "/agent-api/v1/data/stats/overview/":
        if not state.overview:
            raise dq.ServiceQueryError("unavailable", "Overview snapshot is missing.", 503)
        return 200, state.overview

    if norm == "/agent-api/v1/data/datasets/":
        with state.conn_provider() as conn:
            return 200, {"results": dq.fetch_datasets(conn)}

    if norm == "/agent-api/v1/data/phages/":
        page = _int_param(qs, "page", 1)
        pagesize = min(_int_param(qs, "pagesize", 20), 50)
        allowed = {
            "accession", "dataset", "completeness", "lifestyle",
            "taxonomy", "host", "cluster", "length_min", "length_max", "gc_min", "gc_max",
        }
        filters = {k: v for k, v in qs.items() if k in allowed and v != ""}
        with state.conn_provider() as conn:
            res = dq.query_phages(
                conn,
                filters,
                page=page,
                pagesize=pagesize,
                max_pagesize=50,
                include_total=True,
                include_host_ranks=False,
            )
        nxt, prev = _page_links(base_url, "/agent-api/v1/data/phages/", qs, page, pagesize, res["count"])
        return 200, {"count": res["count"], "next": nxt, "previous": prev, "results": res["results"]}

    m_phage = _RE_PHAGE_ID.match(path)
    if m_phage:
        pid = int(m_phage.group(1))
        with state.conn_provider() as conn:
            return 200, dq.get_phage_detail(conn, phage_id=pid)

    m_sub = _RE_PHAGE_SUB.match(path)
    if m_sub:
        pid = int(m_sub.group(1))
        sub = m_sub.group(2)
        if sub == "proteins":
            page = _int_param(qs, "page", 1)
            pagesize = min(_int_param(qs, "pagesize", 20), 50)
            inc_seq = str(qs.get("include_sequence", "")).lower() in {"1", "true", "yes"}
            with state.conn_provider() as conn:
                res = dq.query_phage_proteins(
                    conn, phage_id=pid, include_sequence=inc_seq, page=page, pagesize=pagesize, max_pagesize=50
                )
            nxt, prev = _page_links(base_url, f"/agent-api/v1/data/phages/{pid}/proteins/", qs, page, pagesize, res["count"])
            return 200, {"count": res["count"], "next": nxt, "previous": prev, "results": res["results"]}
        if sub == "features":
            ftype = qs.get("type", "")
            if not ftype:
                raise dq.ServiceQueryError("invalid_parameter", "type is required.")
            page = _int_param(qs, "page", 1)
            pagesize = min(_int_param(qs, "pagesize", 20), 50)
            with state.conn_provider() as conn:
                res = dq.query_features(
                    conn,
                    feature_type=ftype,
                    phage_id=pid,
                    csv_download_root=state.csv_download_root,
                    page=page,
                    pagesize=pagesize,
                )
            nxt, prev = _page_links(base_url, f"/agent-api/v1/data/phages/{pid}/features/", qs, page, pagesize, res["count"])
            return 200, {"count": res["count"], "next": nxt, "previous": prev, "results": res["results"]}
        if sub == "sequence":
            max_bases = _int_param(qs, "max_bases", 20000)
            with state.conn_provider() as conn:
                seq = dq.read_phage_sequence_preview(
                    conn,
                    phage_id=pid,
                    fasta_root=state.fasta_root,
                    fasta_ext_root=state.fasta_ext_root,
                    max_bases=max_bases,
                )
            seq.pop("dataset", None)
            return 200, seq

    m_prot = _RE_PROTEIN_ID.match(path)
    if m_prot:
        prot_id = m_prot.group(1)
        inc_seq = str(qs.get("include_sequence", "")).lower() in {"1", "true", "yes"}
        with state.conn_provider() as conn:
            rows = dq.fetch_proteins_by_ids(conn, [prot_id], include_sequence=inc_seq)
        if not rows:
            raise dq.ServiceQueryError("not_found", "Protein was not found in clustered families.", 404)
        row = dict(rows[0])
        row.pop("dataset", None)
        return 200, row

    if norm == "/agent-api/v1/data/hosts/":
        rank = qs.get("rank", "")
        if not rank:
            raise dq.ServiceQueryError("invalid_parameter", "rank is required.")
        page = _int_param(qs, "page", 1)
        pagesize = min(_int_param(qs, "pagesize", 20), 50)
        with state.conn_provider() as conn:
            res = dq.query_hosts(conn, rank=rank, name=qs.get("name"), page=page, pagesize=pagesize)
        nxt, prev = _page_links(base_url, "/agent-api/v1/data/hosts/", qs, page, pagesize, res["count"])
        return 200, {"count": res["count"], "next": nxt, "previous": prev, "results": res["results"]}

    if norm == "/agent-api/v1/data/families/":
        prot_id = qs.get("protein_id", "")
        if not prot_id:
            raise dq.ServiceQueryError("invalid_parameter", "protein_id is required.")
        with state.conn_provider() as conn:
            fam = dq.search_protein_families(conn, protein_id=prot_id)
        return 200, fam

    m_fam = _RE_FAMILY_ID.match(path)
    if m_fam:
        fid = m_fam.group(1)
        with state.conn_provider() as conn:
            return 200, dq.search_protein_families(conn, family_id=fid)

    m_fmem = _RE_FAMILY_MEMBERS.match(path)
    if m_fmem:
        fid = m_fmem.group(1)
        page = _int_param(qs, "page", 1)
        pagesize = min(_int_param(qs, "pagesize", 20), 50)
        with state.conn_provider() as conn:
            res = dq.list_family_members(
                conn, family_id=fid, dataset=qs.get("dataset"), page=page, pagesize=pagesize, max_pagesize=50
            )
        nxt, prev = _page_links(base_url, f"/agent-api/v1/data/families/{fid}/members/", qs, page, pagesize, res["count"])
        return 200, {"count": res["count"], "next": nxt, "previous": prev, "results": res["results"]}

    if norm == "/agent-api/v1/data/structures/available/":
        single = qs.get("protein_id")
        many = qs.get("protein_ids")
        if bool(single) == bool(many):
            raise dq.ServiceQueryError("invalid_parameter", "Provide protein_id or protein_ids, not both.")
        ids = [single] if single else [p.strip() for p in str(many).split(",") if p.strip()]
        if not 1 <= len(ids) <= 20:
            raise dq.ServiceQueryError("invalid_parameter", "Provide 1..20 protein identifiers.")
        with state.conn_provider() as conn:
            rows = dq.resolve_structures(conn, ids, pdb_host_prefix=state.pdb_host_prefix)
        slim = []
        for r in rows:
            if r.get("available"):
                slim.append(
                    {
                        "protein_id": r["protein_id"],
                        "available": True,
                        "source": r["source"],
                        "method": r["method"],
                        "confidence_label": r["confidence_label"],
                    }
                )
            else:
                slim.append({"protein_id": r["protein_id"], "available": False})
        return 200, {"results": slim}

    return 404, {"error": {"code": "not_found", "message": f"Unknown endpoint: {path}"}}


def _handle_post(
    state: DataServiceState,
    path: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    norm = path.rstrip("/") + "/"

    if norm == "/agent-api/v1/data/v2/phages/query/":
        export_mode = bool(payload.get("export_mode", False))
        max_ps = 2000 if export_mode else 500
        page = _int_param(payload, "page", 1)
        pagesize = _int_param(payload, "pagesize", 50)
        include_total = bool(payload.get("include_total", False))
        sort_by = str(payload.get("sort_by") or "id").strip()
        raw_filters = payload.get("filters")
        filters: dict[str, Any] = raw_filters if isinstance(raw_filters, dict) else payload
        with state.conn_provider() as conn:
            return 200, dq.query_phages(
                conn,
                filters,
                page=page,
                pagesize=pagesize,
                max_pagesize=max_ps,
                include_total=include_total,
                include_host_ranks=bool(payload.get("include_host_ranks", True)),
                sort_by=sort_by,
            )

    if norm == "/agent-api/v1/data/v2/phages/detail/":
        pid = payload.get("phage_id")
        acc = payload.get("accession")
        with state.conn_provider() as conn:
            return 200, dq.get_phage_detail(
                conn,
                phage_id=_int_param(payload, "phage_id", 0) if pid is not None else None,
                accession=str(acc) if acc else None,
                include_host_ranks=True,
            )

    if norm == "/agent-api/v1/data/v2/phages/aggregate/":
        group_by = str(payload.get("group_by") or "").strip()
        raw_filters = payload.get("filters")
        filters = raw_filters if isinstance(raw_filters, dict) else {}
        limit = _int_param(payload, "limit", 50)
        with state.conn_provider() as conn:
            return 200, dq.aggregate_phages(
                conn,
                group_by=group_by,
                filters=filters,
                overview=state.overview,
                limit=limit,
            )

    if norm == "/agent-api/v1/data/v2/proteins/query/":
        mode = str(payload.get("mode") or "by_phage").strip()
        inc_seq = bool(payload.get("include_sequence", False))
        full_seq = bool(payload.get("full_sequence", False))
        page = _int_param(payload, "page", 1)
        pagesize = _int_param(payload, "pagesize", 50)
        with state.conn_provider() as conn:
            if mode == "by_phage":
                pid = payload.get("phage_id")
                return 200, dq.query_phage_proteins(
                    conn,
                    phage_id=_int_param(payload, "phage_id", 0) if pid is not None else None,
                    accession=payload.get("accession"),
                    dataset=payload.get("dataset"),
                    product_query=payload.get("product_query"),
                    function_class=payload.get("function_class"),
                    include_sequence=inc_seq,
                    full_sequence=full_seq,
                    page=page,
                    pagesize=pagesize,
                    max_pagesize=1000,
                )
            if mode == "by_ids":
                ids = payload.get("protein_ids") or []
                if not isinstance(ids, list):
                    raise dq.ServiceQueryError("invalid_parameter", "protein_ids must be a list.")
                rows = dq.fetch_proteins_by_ids(
                    conn,
                    ids,
                    dataset_hint=payload.get("dataset"),
                    include_sequence=inc_seq,
                    full_sequence=full_seq,
                )
                return 200, {"count": len(rows), "results": rows}
            if mode == "search_families":
                min_m = _int_param(payload, "min_members", 0) if payload.get("min_members") is not None else None
                min_d = _int_param(payload, "min_datasets", 0) if payload.get("min_datasets") is not None else None
                return 200, dq.search_protein_families(
                    conn,
                    protein_id=payload.get("protein_id"),
                    family_id=payload.get("family_id"),
                    keyword=payload.get("keyword"),
                    dominant_class=payload.get("dominant_class"),
                    min_members=min_m,
                    min_datasets=min_d,
                    non_singleton_only=bool(payload.get("non_singleton_only", True)),
                    page=page,
                    pagesize=pagesize,
                )
            if mode == "family_members":
                return 200, dq.list_family_members(
                    conn,
                    family_id=str(payload.get("family_id") or ""),
                    subfamily_id=payload.get("subfamily_id"),
                    dataset=payload.get("dataset"),
                    include_sequence=inc_seq,
                    full_sequence=full_seq,
                    page=page,
                    pagesize=pagesize,
                    max_pagesize=200,
                )
            raise dq.ServiceQueryError("invalid_parameter", f"Unknown protein query mode: {mode!r}.")

    if norm == "/agent-api/v1/data/v2/features/query/":
        pid = payload.get("phage_id")
        page = _int_param(payload, "page", 1)
        pagesize = _int_param(payload, "pagesize", 50)
        with state.conn_provider() as conn:
            return 200, dq.query_features(
                conn,
                feature_type=str(payload.get("feature_type") or payload.get("type") or ""),
                phage_id=_int_param(payload, "phage_id", 0) if pid is not None else None,
                accession=payload.get("accession"),
                dataset=payload.get("dataset"),
                keyword=payload.get("keyword"),
                csv_download_root=state.csv_download_root,
                page=page,
                pagesize=pagesize,
            )

    if norm == "/agent-api/v1/data/v2/structures/resolve/":
        ids = payload.get("protein_ids") or ([payload["protein_id"]] if payload.get("protein_id") else [])
        if not isinstance(ids, list):
            raise dq.ServiceQueryError("invalid_parameter", "protein_ids must be a list.")
        with state.conn_provider() as conn:
            rows = dq.resolve_structures(conn, ids, pdb_host_prefix=state.pdb_host_prefix)
        return 200, {"count": len(rows), "results": rows}

    return 404, {"error": {"code": "not_found", "message": f"Unknown endpoint: {path}"}}


def make_handler(state: DataServiceState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            status, payload = dispatch_request(state, "GET", self.path, dict(self.headers))
            self._send_json(status, payload)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            body = self.rfile.read(min(length, MAX_BODY_BYTES + 1)) if length > 0 else b""
            status, payload = dispatch_request(state, "POST", self.path, dict(self.headers), body)
            self._send_json(status, payload)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            _ = self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("%s - %s", self.address_string(), format % args)

    return _Handler


def main() -> None:  # pragma: no cover - host entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    psycopg2_pool = importlib.import_module("psycopg2.pool")

    api_key = os.environ.get("DATA_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("refusing to start: DATA_API_KEY is empty")

    try:
        max_conn = int(os.environ.get("PG_MAX_CONN", "4"))
        pg_port = int(os.environ.get("PGPORT", "5432"))
        bind_port = int(os.environ.get("BIND_PORT", "18326"))
    except ValueError as exc:
        raise SystemExit(f"invalid numeric environment configuration: {exc}") from exc

    pool = psycopg2_pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=max_conn,
        dbname=os.environ.get("PGDATABASE", "phage"),
        user=os.environ.get("PGUSER_RO", "phage_ro"),
        password=os.environ.get("PGPASSWORD_RO", ""),
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=pg_port,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )

    @contextlib.contextmanager
    def borrow_conn() -> Iterator[Any]:
        conn = pool.getconn()
        try:
            conn.autocommit = True
            yield conn
        finally:
            pool.putconn(conn)

    with borrow_conn() as conn:
        err = dq.verify_readonly_connection(conn)
        if err:
            raise SystemExit(f"refusing to start data service: {err}")

    overview: dict[str, Any] = {}
    if OVERVIEW_PATH.is_file():
        try:
            loaded = json.loads(OVERVIEW_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                overview = loaded
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load overview.json: %s", exc)

    fasta_env = os.environ.get("PHAGE_FASTA_ROOT", "").strip()
    fasta_ext_env = os.environ.get("PHAGE_FASTA_EXT_ROOT", "").strip()
    csv_env = os.environ.get("PHAGE_CSV_DOWNLOAD_ROOT", "").strip()
    state = DataServiceState(
        api_key=api_key,
        conn_provider=borrow_conn,
        overview=overview,
        fasta_root=Path(fasta_env) if fasta_env else None,
        fasta_ext_root=Path(fasta_ext_env) if fasta_ext_env else None,
        csv_download_root=Path(csv_env) if csv_env else None,
        pdb_host_prefix=os.environ.get("PHAGE_PDB_HOST_PREFIX", "/data4/back_up_platform/esmfold_pdb/"),
    )

    bind_host = os.environ.get("BIND_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(state))
    logger.info("PhageScope read-only data service listening on %s:%d", bind_host, bind_port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        pool.closeall()


if __name__ == "__main__":
    main()
