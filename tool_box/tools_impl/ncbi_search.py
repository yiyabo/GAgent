"""NCBI Entrez search, record summaries and taxon lineages (read-only).

Every request goes through ``ncbi_eutils`` (one rate limit for the whole
process, retries on 429). The tool returns compact metadata only: sequences and
annotation are downloaded with ``sequence_fetch``, assembly files with
``url_fetch`` from the https links returned here.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import httpx

from . import ncbi_eutils

logger = logging.getLogger(__name__)

TOOL_NAME = "ncbi_search"
ACTIONS = ("search", "summary", "taxonomy")
DATABASES = ("nuccore", "protein", "assembly", "taxonomy", "gene", "bioproject", "biosample", "sra")
DEFAULT_RETMAX = 20
MAX_RETMAX = 100
MAX_IDS = 200
MAX_TERM_CHARS = 1000
MAX_TAXA = 5
# esearch serves only the first 10,000 matches of a query (retstart + retmax).
ESEARCH_WINDOW = 10_000
# Above this many ids the request goes as POST so the URL stays short.
POST_ABOVE_IDS = 100
REQUEST_TIMEOUT = 30.0

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
_TAXID_PATTERN = re.compile(r"^\d{1,10}$")
_ASSEMBLY_ACCESSION = re.compile(r"^GC[AF]_\d{9}(?:\.\d+)?$", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_GENERIC_FIELD_CHARS = 300
_GENERIC_FIELDS = 12
_NO_DATE = "1/01/01 00:00"
_FTP_PREFIX = "ftp://ftp.ncbi.nlm.nih.gov/"


class NcbiSearchError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True)


# ------------------------------------------------------------------ input checks
def _parse_action(value: Any) -> str:
    action = str(value or "").strip().lower()
    if action not in ACTIONS:
        raise NcbiSearchError("action must be one of: " + ", ".join(ACTIONS), "invalid_action")
    return action


def _parse_db(value: Any) -> str:
    db = str(value or "nuccore").strip().lower()
    if db not in DATABASES:
        raise NcbiSearchError("db must be one of: " + ", ".join(DATABASES), "invalid_db")
    return db


def _parse_term(value: Any) -> Optional[str]:
    if value is None:
        return None
    term = str(value).strip()
    if not term:
        return None
    if len(term) > MAX_TERM_CHARS:
        raise NcbiSearchError(f"term is longer than {MAX_TERM_CHARS} characters", "invalid_term")
    if _CONTROL_CHARS.search(term):
        raise NcbiSearchError("term contains control characters", "invalid_term")
    return term


def _parse_ids(value: Any, *, pattern: re.Pattern = _ID_PATTERN) -> List[str]:
    if value is None:
        return []
    raw: List[str] = []
    items = [value] if isinstance(value, (str, int)) else list(value) if isinstance(value, (list, tuple, set)) else []
    for item in items:
        raw.extend(part for part in re.split(r"[\s,;]+", str(item).strip()) if part)
    ids: List[str] = []
    seen = set()
    for item in raw:
        if not pattern.match(item):
            raise NcbiSearchError(f"invalid id: {item[:60]}", "invalid_ids")
        if item.upper() not in seen:
            seen.add(item.upper())
            ids.append(item)
    if len(ids) > MAX_IDS:
        raise NcbiSearchError(f"at most {MAX_IDS} ids per call", "too_many_ids")
    return ids


def _parse_int(value: Any, *, default: int, low: int, high: int, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise NcbiSearchError(f"{name} must be an integer", f"invalid_{name}") from None
    return max(low, min(high, number))


# ------------------------------------------------------------------ row mappers
def _compact(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {}, 0, "0", _NO_DATE)}


def _https(url: Any) -> Optional[str]:
    if isinstance(url, str) and url.startswith(_FTP_PREFIX):
        return "https://ftp.ncbi.nlm.nih.gov/" + url[len(_FTP_PREFIX):]
    return url if isinstance(url, str) and url else None


def _sequence_row(uid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    return _compact(
        {
            "uid": uid,
            "accession": rec.get("accessionversion") or rec.get("caption"),
            "title": rec.get("title"),
            "organism": rec.get("organism"),
            "taxid": rec.get("taxid"),
            "length": rec.get("slen"),
            "moltype": rec.get("moltype"),
            "topology": rec.get("topology"),
            "completeness": rec.get("completeness"),
            "source_db": rec.get("sourcedb"),
            "updated": rec.get("updatedate"),
        }
    )


def _assembly_row(uid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    released = rec.get("asmreleasedate_refseq")
    if not released or released == _NO_DATE:
        released = rec.get("asmreleasedate_genbank")
    return _compact(
        {
            "uid": uid,
            "accession": rec.get("assemblyaccession"),
            "name": rec.get("assemblyname"),
            "organism": rec.get("organism"),
            "species": rec.get("speciesname"),
            "taxid": rec.get("taxid"),
            "status": rec.get("assemblystatus"),
            "refseq_category": None if rec.get("refseq_category") == "na" else rec.get("refseq_category"),
            "released": released,
            "contig_n50": rec.get("contign50"),
            "ftp_refseq": _https(rec.get("ftppath_refseq")),
            "ftp_genbank": _https(rec.get("ftppath_genbank")),
        }
    )


def _taxonomy_row(uid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    return _compact(
        {
            "taxid": rec.get("taxid") or uid,
            "scientific_name": rec.get("scientificname"),
            "common_name": rec.get("commonname"),
            "rank": rec.get("rank"),
            "division": rec.get("division"),
            "genus": rec.get("genus"),
            "species": rec.get("species"),
        }
    )


def _gene_row(uid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    organism = rec.get("organism") if isinstance(rec.get("organism"), dict) else {}
    return _compact(
        {
            "uid": uid,
            "name": rec.get("name"),
            "description": rec.get("description"),
            "organism": organism.get("scientificname"),
            "taxid": organism.get("taxid"),
            "chromosome": rec.get("chromosome"),
            "map_location": rec.get("maplocation"),
            "aliases": rec.get("otheraliases"),
        }
    )


def _generic_row(uid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"uid": uid}
    for key, value in rec.items():
        if len(row) > _GENERIC_FIELDS:
            break
        if key == "uid" or key.endswith("xml"):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            row[key] = value
        elif isinstance(value, str) and value.strip() and len(value) <= _GENERIC_FIELD_CHARS:
            row[key] = value.strip()
    return _compact(row)


_MAPPERS = {
    "nuccore": _sequence_row,
    "protein": _sequence_row,
    "assembly": _assembly_row,
    "taxonomy": _taxonomy_row,
    "gene": _gene_row,
}


# ------------------------------------------------------------------ E-utility calls
def _json(response: httpx.Response, endpoint: str) -> Dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise NcbiSearchError(f"NCBI {endpoint} did not return JSON", "ncbi_bad_response") from None
    if not isinstance(payload, dict):
        raise NcbiSearchError(f"NCBI {endpoint} returned an unexpected payload", "ncbi_bad_response")
    return payload


def _search_notes(result: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    errors = result.get("errorlist") or {}
    warnings = result.get("warninglist") or {}
    for label, values in (
        ("phrase not found", errors.get("phrasesnotfound")),
        ("field not found", errors.get("fieldsnotfound")),
        ("phrase ignored", warnings.get("phrasesignored")),
        ("quoted phrase not found", warnings.get("quotedphrasesnotfound")),
    ):
        for value in values or []:
            notes.append(f"{label}: {value}")
    for message in warnings.get("outputmessages") or []:
        notes.append(str(message))
    return notes


async def _esearch(client: httpx.AsyncClient, db: str, term: str, *, retmax: int, retstart: int = 0) -> Dict[str, Any]:
    response = await ncbi_eutils.request(
        "esearch.fcgi",
        {"db": db, "term": term, "retmode": "json", "retmax": retmax, "retstart": retstart},
        client=client,
        timeout=REQUEST_TIMEOUT,
    )
    payload = _json(response, "esearch")
    result = payload.get("esearchresult")
    if not isinstance(result, dict):
        raise NcbiSearchError(str(payload.get("error") or "NCBI esearch returned no result"), "ncbi_bad_response")
    if result.get("ERROR"):
        raise NcbiSearchError(f"NCBI esearch: {result['ERROR']}", "ncbi_query_error")
    return result


async def _esummary(client: httpx.AsyncClient, db: str, uids: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not uids:
        return [], []
    response = await ncbi_eutils.request(
        "esummary.fcgi",
        {"db": db, "id": ",".join(uids), "retmode": "json"},
        client=client,
        timeout=REQUEST_TIMEOUT,
        method="POST" if len(uids) > POST_ABOVE_IDS else "GET",
    )
    payload = _json(response, "esummary")
    notes = [str(item) for item in payload.get("esummaryresult") or []]
    if payload.get("error"):
        notes.append(str(payload["error"]))
    result = payload.get("result") or {}
    mapper = _MAPPERS.get(db, _generic_row)
    rows: List[Dict[str, Any]] = []
    for uid in result.get("uids") or []:
        record = result.get(str(uid))
        if not isinstance(record, dict):
            continue
        if record.get("error"):
            notes.append(f"{uid}: {record['error']}")
            continue
        rows.append(mapper(str(uid), record))
    return rows, notes


def _missing(requested: Sequence[str], rows: Sequence[Dict[str, Any]]) -> List[str]:
    found = set()
    for row in rows:
        for key in ("accession", "uid", "taxid"):
            value = str(row.get(key) or "").upper()
            if value:
                found.add(value)
                found.add(value.split(".")[0])
    return [item for item in requested if item.upper() not in found]


# ------------------------------------------------------------------ actions
async def _action_search(client: httpx.AsyncClient, db: str, term: Optional[str], retmax: int, retstart: int) -> Dict[str, Any]:
    if not term:
        raise NcbiSearchError("search needs a term (an Entrez query)", "missing_term")
    if retstart >= ESEARCH_WINDOW:
        raise NcbiSearchError(
            f"NCBI returns only the first {ESEARCH_WINDOW} matches of a query; narrow the term instead of paging further",
            "retstart_out_of_range",
        )
    retmax = min(retmax, ESEARCH_WINDOW - retstart)
    result = await _esearch(client, db, term, retmax=retmax, retstart=retstart)
    ids = [str(item) for item in result.get("idlist") or []]
    rows, summary_notes = await _esummary(client, db, ids)
    count = int(result.get("count") or 0)
    payload: Dict[str, Any] = {
        "success": True,
        "tool": TOOL_NAME,
        "action": "search",
        "db": db,
        "term": term,
        "count": count,
        "retstart": retstart,
        "returned": len(rows),
        "query_translation": result.get("querytranslation"),
        "records": rows,
    }
    notes = _search_notes(result) + summary_notes
    if notes:
        payload["notes"] = notes
    if retstart + len(ids) < min(count, ESEARCH_WINDOW):
        payload["next_retstart"] = retstart + len(ids)
    return payload


async def _action_summary(client: httpx.AsyncClient, db: str, ids: List[str]) -> Dict[str, Any]:
    if not ids:
        raise NcbiSearchError("summary needs ids (UIDs or accessions)", "missing_ids")
    uids = list(ids)
    notes: List[str] = []
    if db == "assembly":
        accessions = [item for item in ids if _ASSEMBLY_ACCESSION.match(item)]
        uids = [item for item in ids if not _ASSEMBLY_ACCESSION.match(item)]
        if accessions:
            term = " OR ".join(f"{item}[Assembly Accession]" for item in accessions)
            result = await _esearch(client, "assembly", term, retmax=len(accessions))
            uids.extend(str(item) for item in result.get("idlist") or [])
    rows, summary_notes = await _esummary(client, db, uids)
    notes.extend(summary_notes)
    payload: Dict[str, Any] = {
        "success": True,
        "tool": TOOL_NAME,
        "action": "summary",
        "db": db,
        "requested": len(ids),
        "returned": len(rows),
        "records": rows,
    }
    missing = _missing(ids, rows)
    if missing:
        payload["missing"] = missing
    if notes:
        payload["notes"] = notes
    return payload


def _parse_taxa(xml_text: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise NcbiSearchError("NCBI taxonomy efetch did not return XML", "ncbi_bad_response") from None
    taxa: List[Dict[str, Any]] = []
    for taxon in root.findall("Taxon"):
        other_names: List[str] = []
        names_node = taxon.find("OtherNames")
        if names_node is not None:
            for child in names_node:
                if child.text and child.text.strip() and child.tag != "Name":
                    other_names.append(child.text.strip())
        ranked = [
            {"rank": node.findtext("Rank"), "name": node.findtext("ScientificName"), "taxid": node.findtext("TaxId")}
            for node in taxon.findall("LineageEx/Taxon")
            if (node.findtext("Rank") or "no rank") != "no rank"
        ]
        taxa.append(
            _compact(
                {
                    "taxid": taxon.findtext("TaxId"),
                    "scientific_name": taxon.findtext("ScientificName"),
                    "rank": taxon.findtext("Rank"),
                    "division": taxon.findtext("Division"),
                    "genetic_code": taxon.findtext("GeneticCode/GCId"),
                    "parent_taxid": taxon.findtext("ParentTaxId"),
                    "lineage": taxon.findtext("Lineage"),
                    "ranked_lineage": ranked,
                    "other_names": other_names[:5],
                }
            )
        )
    return taxa


async def _action_taxonomy(client: httpx.AsyncClient, term: Optional[str], ids: List[str]) -> Dict[str, Any]:
    notes: List[str] = []
    taxids = list(ids)
    if not taxids:
        if not term:
            raise NcbiSearchError("taxonomy needs a taxon name (term) or taxids (ids)", "missing_term")
        result = await _esearch(client, "taxonomy", term, retmax=MAX_TAXA)
        taxids = [str(item) for item in result.get("idlist") or []]
        notes.extend(_search_notes(result))
    taxa: List[Dict[str, Any]] = []
    if taxids:
        response = await ncbi_eutils.request(
            "efetch.fcgi",
            {"db": "taxonomy", "id": ",".join(taxids), "retmode": "xml"},
            client=client,
            timeout=REQUEST_TIMEOUT,
        )
        taxa = _parse_taxa(response.text)
    payload: Dict[str, Any] = {
        "success": True,
        "tool": TOOL_NAME,
        "action": "taxonomy",
        "query": term or ",".join(ids),
        "returned": len(taxa),
        "taxa": taxa,
    }
    if ids:
        missing = [item for item in ids if item not in {str(t.get("taxid")) for t in taxa}]
        if missing:
            payload["missing"] = missing
    if notes:
        payload["notes"] = notes
    return payload


async def ncbi_search_handler(
    action: str,
    db: str = "nuccore",
    term: Optional[str] = None,
    ids: Optional[Any] = None,
    retmax: Optional[Any] = None,
    retstart: Optional[Any] = None,
    session_id: Optional[str] = None,
    tool_context: Any = None,
) -> Dict[str, Any]:
    owner = session_id or getattr(tool_context, "session_id", None)
    try:
        chosen = _parse_action(action)
        database = _parse_db(db)
        query = _parse_term(term)
        id_list = _parse_ids(ids, pattern=_TAXID_PATTERN if chosen == "taxonomy" else _ID_PATTERN)
        limit = _parse_int(retmax, default=DEFAULT_RETMAX, low=1, high=MAX_RETMAX, name="retmax")
        offset = _parse_int(retstart, default=0, low=0, high=ESEARCH_WINDOW, name="retstart")
        with ncbi_eutils.owner_scope(owner):
            async with _new_client() as client:
                if chosen == "search":
                    return await _action_search(client, database, query, limit, offset)
                if chosen == "summary":
                    return await _action_summary(client, database, id_list)
                return await _action_taxonomy(client, query, id_list)
    except NcbiSearchError as exc:
        return {"success": False, "tool": TOOL_NAME, "error": str(exc), "error_code": exc.code}
    except ncbi_eutils.NcbiRequestError as exc:
        failure: Dict[str, Any] = {
            "success": False,
            "tool": TOOL_NAME,
            "error": str(exc),
            "error_code": "ncbi_rate_limited" if exc.rate_limited else "ncbi_http_error",
        }
        if exc.rate_limited:
            failure["retry_hint"] = (
                "NCBI is limiting requests and the tool already retried. Wait before trying again "
                "and combine ids into fewer calls."
            )
        return failure


PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "search: run an Entrez query (term) and return the matching records' summaries. "
                "summary: summaries for given ids (UIDs or accessions; GCF_/GCA_ accessions for assembly). "
                "taxonomy: lineage, rank and names for a taxon name (term) or taxids (ids)."
            ),
        },
        "db": {
            "type": "string",
            "enum": list(DATABASES),
            "default": "nuccore",
            "description": "Entrez database for search and summary (taxonomy always uses the taxonomy database).",
        },
        "term": {
            "type": "string",
            "description": (
                "Entrez query, e.g. 'Escherichia phage T7[orgn] AND complete genome[title]', "
                "'Klebsiella phage[title] AND complete genome[title] AND srcdb_refseq[prop]', "
                "'Viruses[orgn] AND Klebsiella[title] AND 30000:60000[slen]'. [orgn] needs an existing taxon "
                "name, so put host names in [title]. For taxonomy, a taxon name."
            ),
        },
        "ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": f"UIDs or accessions for summary, or taxids for taxonomy (at most {MAX_IDS}).",
        },
        "retmax": {
            "type": "integer",
            "default": DEFAULT_RETMAX,
            "minimum": 1,
            "maximum": MAX_RETMAX,
            "description": "How many matches search returns (the total is in count).",
        },
        "retstart": {
            "type": "integer",
            "default": 0,
            "minimum": 0,
            "description": "Offset into the matches for paging through a search (use next_retstart).",
        },
    },
    "required": ["action"],
}

ncbi_search_tool = {
    "name": TOOL_NAME,
    "description": (
        "Search NCBI Entrez and read record summaries: find accessions with an Entrez query "
        "(nuccore, protein, assembly, taxonomy, gene, bioproject, biosample, sra), summarize known "
        "accessions or UIDs, or look up a taxon's lineage. Returns compact metadata only: download "
        "sequences or GenBank records with sequence_fetch, and assembly files from the https links it "
        "returns with url_fetch. All NCBI calls share one rate limit, so summarize many ids in one call."
    ),
    "category": "bioinformatics",
    "parameters_schema": PARAMETERS_SCHEMA,
    "handler": ncbi_search_handler,
    "tags": ["ncbi", "entrez", "search", "accession", "taxonomy", "assembly", "genome", "metadata"],
    "examples": [
        "Find complete RefSeq genomes of Klebsiella phages",
        "Summarize NC_001416.1 and NC_001604.1",
        "Lineage of Lambdavirus lambda",
    ],
}
