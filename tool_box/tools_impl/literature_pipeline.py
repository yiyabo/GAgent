"""
Literature Pipeline Tool
------------------------
Build a submission-grade literature pack for review writing:
- Query PubMed via NCBI E-utilities (ESearch + EFetch XML)
- Optionally merge Europe PMC REST search hits (same query string)
- Optionally merge bioRxiv preprints (api.biorxiv.org date-sliced API + keyword filter)
- Extract metadata (title/authors/year/journal/doi/pmid/pmcid/abstract)
- Generate stable citekeys and write:
  - library.jsonl (structured records)
  - references.bib (BibTeX with citekeys)
  - evidence.md (lightweight evidence inventory with citekeys)
- Download OA PDFs via PMC when PMCID is available; bioRxiv PDFs via journal PDF URLs when DOI is 10.1101/*

Design principles:
- No fabricated metadata: everything comes from NCBI/PMC/Europe PMC/bioRxiv API responses.
- OA-only PDF download for PMC; bioRxiv uses publisher-hosted full.pdf links for 10.1101 DOIs.
"""

from __future__ import annotations

import asyncio
import json
import io
import logging
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"
_MAX_FULLTEXT_CHARS = 80_000
_PMC_OA_UTILITY_TIMEOUT = 30.0
_PMC_OA_PDF_TIMEOUT = 30.0
_PMC_OA_ARCHIVE_TIMEOUT = 45.0
_PMC_ARTICLE_PAGE_TIMEOUT = 20.0
_PMC_LEGACY_PDF_TIMEOUT = 30.0
_PMC_DOWNLOAD_CONCURRENCY = 6

# ---------------------------------------------------------------------------
# NCBI rate limiter — global singleton
# NCBI allows 3 requests/sec without API key, 10 with key.
# We use a conservative 2.5 req/s to stay well within limits across
# concurrent literature_pipeline invocations.
# ---------------------------------------------------------------------------
_NCBI_RATE_LIMIT_INTERVAL = 0.4  # seconds between requests (≈2.5 req/s)
_NCBI_MAX_RETRIES = 3
_NCBI_RETRY_BACKOFF_BASE = 2.0  # seconds; doubling each retry
_ncbi_rate_lock = asyncio.Lock()
_ncbi_last_request_time: float = 0.0


async def _ncbi_throttle() -> None:
    """Ensure at least _NCBI_RATE_LIMIT_INTERVAL seconds between NCBI calls."""
    global _ncbi_last_request_time
    import time

    async with _ncbi_rate_lock:
        now = time.monotonic()
        elapsed = now - _ncbi_last_request_time
        if elapsed < _NCBI_RATE_LIMIT_INTERVAL:
            await asyncio.sleep(_NCBI_RATE_LIMIT_INTERVAL - elapsed)
        _ncbi_last_request_time = time.monotonic()
_COVERAGE_THRESHOLDS = {
    "min_total_studies": 15,
    "min_full_text_studies": 6,
    "min_quantitative_studies": 4,
    "min_support_per_core_section": 2,
}
_CORE_REVIEW_SECTIONS = (
    "introduction",
    "method",
    "experiment",
    "result",
    "discussion",
    "conclusion",
)
_STUDY_TYPE_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brandomized\b|\bclinical trial\b|\btrial\b", re.I), "clinical_trial"),
    (re.compile(r"\bcase report\b|\bcase series\b|\bcompassionate use\b", re.I), "clinical_case"),
    (re.compile(r"\bmurine\b|\bmouse\b|\bmice\b|\bmodel\b|\bin vivo\b", re.I), "animal_model"),
    (re.compile(r"\bgalleria mellonella\b|\blarvae\b", re.I), "invertebrate_model"),
    (re.compile(r"\bbiofilm\b|\bin vitro\b|\bplaque assay\b|\bcheckerboard\b|\btime-kill\b", re.I), "in_vitro"),
    (re.compile(r"\bgenom(?:e|ic)\b|\bsequenc(?:e|ing)\b|\bphylogen(?:y|etic)\b", re.I), "genomic_characterization"),
    (re.compile(r"\breview\b|\bsystematic review\b|\bnarrative review\b", re.I), "review"),
]
_MODEL_SYSTEM_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcystic fibrosis\b|\bCF\b", re.I), "cystic_fibrosis"),
    (re.compile(r"\bburn wound\b|\bburn\b", re.I), "burn_wound"),
    (re.compile(r"\bkeratitis\b|\bcorneal\b", re.I), "ocular_model"),
    (re.compile(r"\blung\b|\bpulmonary\b|\bintranasal\b|\bnebul", re.I), "pulmonary_model"),
    (re.compile(r"\bbacteremia\b|\bsepticemia\b|\bsystemic infection\b", re.I), "systemic_model"),
    (re.compile(r"\bgalleria mellonella\b|\blarvae\b", re.I), "galleria_model"),
    (re.compile(r"\bmurine\b|\bmouse\b|\bmice\b", re.I), "murine_model"),
    (re.compile(r"\bbiofilm\b|\bflow-cell\b|\bmicrotiter\b", re.I), "biofilm_assay"),
]
_INTERVENTION_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcocktail\b", re.I), "phage_cocktail"),
    (re.compile(r"\bhydrogel\b|\bbiogel\b", re.I), "hydrogel_delivery"),
    (re.compile(r"\binhalable\b|\bnebul(?:ized|isation|ization)?\b|\baerosol\b", re.I), "airway_delivery"),
    (re.compile(r"\bnanoparticle\b|\bliposome\b|\bencapsulat", re.I), "encapsulated_delivery"),
    (re.compile(r"\bdepolymerase\b", re.I), "depolymerase_engineering"),
    (re.compile(r"\bquorum[- ]quenching\b|\bquorum sensing\b", re.I), "quorum_quenching"),
    (re.compile(r"\bantibiotic\b|\bcombination therapy\b|\bsynergy\b", re.I), "phage_antibiotic_combination"),
]
_RECEPTOR_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\blptd\b", re.I), "LptD"),
    (re.compile(r"\bpsl\b", re.I), "Psl"),
    (re.compile(r"\blipopolysaccharide\b|\bLPS\b", re.I), "LPS"),
    (re.compile(r"\btail fiber\b", re.I), "tail_fiber"),
    (re.compile(r"\bcrisper\b|\bcrispr\b", re.I), "CRISPR"),
    (re.compile(r"\badsorption\b|\breceptor\b|\bhost range\b", re.I), "host_range_receptor"),
]
_QUANTITATIVE_HINTS = (
    "cfu",
    "survival",
    "log",
    "fold",
    "%",
    "percent",
    "reduction",
    "increase",
    "decrease",
    "moi",
    "dose",
    "hours",
    "days",
    "pfu",
    "count",
    "rate",
)
_LIMITATION_HINTS = (
    "limitation",
    "limited",
    "lack",
    "unclear",
    "heterogeneity",
    "not available",
    "not reported",
    "however",
    "small",
    "preclude",
    "inconsistent",
)


@dataclass
class PaperRecord:
    citekey: str
    title: str
    authors: List[str]
    year: Optional[str]
    journal: Optional[str]
    doi: Optional[str]
    pmid: Optional[str]
    pmcid: Optional[str]
    abstract: Optional[str]
    url: Optional[str]
    preprint_version: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "citekey": self.citekey,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "journal": self.journal,
            "doi": self.doi,
            "pmid": self.pmid,
            "pmcid": self.pcid_safe(),
            "abstract": self.abstract,
            "url": self.url,
        }
        if self.preprint_version:
            d["preprint_version"] = self.preprint_version
        return d

    def pcid_safe(self) -> Optional[str]:
        return self.pmcid


def _safe_text(node: Optional[ET.Element]) -> Optional[str]:
    if node is None:
        return None
    text = "".join(node.itertext()).strip()
    return text or None


def _first(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _normalize_year(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", value)
    return m.group(1) if m else None


def _slugify_citekey(text: str) -> str:
    # keep only ASCII letters/digits, collapse separators
    t = re.sub(r"[^A-Za-z0-9]+", "", text or "")
    return t or "Anon"


def _build_citekey(
    authors: List[str],
    year: Optional[str],
    journal: Optional[str],
    seen: Dict[str, int],
) -> str:
    first_author = authors[0] if authors else "Anon"
    base = _slugify_citekey(first_author)
    y = year or "n.d."
    j = _slugify_citekey((journal or "")[:16]) if journal else "Paper"
    key = f"{base}{y}{j}"
    n = seen.get(key, 0)
    seen[key] = n + 1
    if n == 0:
        return key
    # 2nd+ occurrence: a, b, c...
    suffix = chr(ord("a") + min(n, 25))
    return f"{key}{suffix}"


def _extract_article_ids(pubmed_article: ET.Element) -> Dict[str, Optional[str]]:
    ids: Dict[str, Optional[str]] = {"pmid": None, "pmcid": None, "doi": None}
    pmid_node = pubmed_article.find(".//MedlineCitation/PMID")
    ids["pmid"] = _safe_text(pmid_node)

    for aid in pubmed_article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        id_type = (aid.attrib.get("IdType") or "").strip().lower()
        val = _safe_text(aid)
        if not val:
            continue
        if id_type == "doi" and ids["doi"] is None:
            ids["doi"] = val
        elif id_type == "pmc" and ids["pmcid"] is None:
            # PMC IDs are like "PMC1234567"
            ids["pmcid"] = val if val.startswith("PMC") else f"PMC{val}"
        elif id_type == "pubmed" and ids["pmid"] is None:
            ids["pmid"] = val
    return ids


def _extract_authors(pubmed_article: ET.Element) -> List[str]:
    authors: List[str] = []
    for au in pubmed_article.findall(".//Article/AuthorList/Author"):
        last = _safe_text(au.find("LastName"))
        initials = _safe_text(au.find("Initials"))
        coll = _safe_text(au.find("CollectiveName"))
        if coll:
            authors.append(coll)
            continue
        if last and initials:
            authors.append(f"{last} {initials}")
        elif last:
            authors.append(last)
    # de-dup preserve order
    seen: set[str] = set()
    dedup: List[str] = []
    for a in authors:
        if a in seen:
            continue
        seen.add(a)
        dedup.append(a)
    return dedup


def _extract_title(pubmed_article: ET.Element) -> str:
    title = _safe_text(pubmed_article.find(".//Article/ArticleTitle")) or ""
    title = re.sub(r"\s+", " ", title).strip()
    return title or "Untitled"


def _extract_abstract(pubmed_article: ET.Element) -> Optional[str]:
    parts: List[str] = []
    for at in pubmed_article.findall(".//Article/Abstract/AbstractText"):
        label = (at.attrib.get("Label") or "").strip()
        text = _safe_text(at)
        if not text:
            continue
        if label:
            parts.append(f"{label}: {text}")
        else:
            parts.append(text)
    if not parts:
        return None
    joined = "\n".join(parts)
    return joined.strip() or None


def _extract_journal_year(pubmed_article: ET.Element) -> Tuple[Optional[str], Optional[str]]:
    journal = _safe_text(pubmed_article.find(".//Article/Journal/Title"))
    year = _safe_text(pubmed_article.find(".//Article/Journal/JournalIssue/PubDate/Year"))
    if not year:
        year = _safe_text(pubmed_article.find(".//Article/Journal/JournalIssue/PubDate/MedlineDate"))
    year = _normalize_year(year)
    return journal, year


def _bib_escape(text: str) -> str:
    # Minimal BibTeX escaping.
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .strip()
    )


def _authors_to_bib(authors: List[str]) -> str:
    # Convert "Last I" to "Last, I" when possible, preserve collective names.
    out: List[str] = []
    for a in authors:
        if " " in a and not a.lower().endswith("consortium"):
            last, rest = a.split(" ", 1)
            out.append(f"{last}, {rest}")
        else:
            out.append(a)
    return " and ".join(out)


def _record_to_bibtex(rec: PaperRecord) -> str:
    fields: List[Tuple[str, Optional[str]]] = [
        ("title", rec.title),
        ("author", _authors_to_bib(rec.authors) if rec.authors else None),
        ("journal", rec.journal),
        ("year", rec.year),
        ("doi", rec.doi),
        ("pmid", rec.pmid),
        ("pmcid", rec.pmcid),
        ("url", rec.url),
    ]
    body = []
    for k, v in fields:
        if not v:
            continue
        body.append(f"  {k} = {{{_bib_escape(v)}}}")
    return "@article{{{key},\n{body}\n}}\n".format(key=rec.citekey, body=",\n".join(body))


async def _pubmed_esearch(client: httpx.AsyncClient, term: str, retmax: int) -> List[str]:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pubmed",
        "retmode": "json",
        "retmax": str(max(1, min(int(retmax), 500))),
        "term": term,
    }
    last_exc: Optional[Exception] = None
    for attempt in range(_NCBI_MAX_RETRIES):
        await _ncbi_throttle()
        try:
            r = await client.get(url, params=params, timeout=40.0)
            if r.status_code == 429:
                backoff = _NCBI_RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "[LITERATURE] NCBI esearch 429 rate-limited; retrying in %.1fs (attempt %d/%d)",
                    backoff, attempt + 1, _NCBI_MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                continue
            r.raise_for_status()
            payload = r.json()
            ids = payload.get("esearchresult", {}).get("idlist", []) or []
            return [str(x) for x in ids if str(x).strip()]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                last_exc = exc
                backoff = _NCBI_RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "[LITERATURE] NCBI esearch 429; backoff %.1fs (attempt %d/%d)",
                    backoff, attempt + 1, _NCBI_MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                continue
            raise
    raise last_exc or RuntimeError("NCBI esearch failed after retries")


async def _pubmed_efetch_xml(client: httpx.AsyncClient, pmids: List[str]) -> str:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": ",".join(pmids),
    }
    last_exc: Optional[Exception] = None
    for attempt in range(_NCBI_MAX_RETRIES):
        await _ncbi_throttle()
        try:
            r = await client.get(url, params=params, timeout=60.0)
            if r.status_code == 429:
                backoff = _NCBI_RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "[LITERATURE] NCBI efetch 429 rate-limited; retrying in %.1fs (attempt %d/%d)",
                    backoff, attempt + 1, _NCBI_MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                continue
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                last_exc = exc
                backoff = _NCBI_RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "[LITERATURE] NCBI efetch 429; backoff %.1fs (attempt %d/%d)",
                    backoff, attempt + 1, _NCBI_MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                continue
            raise
    raise last_exc or RuntimeError("NCBI efetch failed after retries")


_EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_BIORXIV_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "or",
        "for",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "into",
        "using",
        "based",
        "between",
        "among",
        "within",
        "without",
        "over",
        "under",
        "about",
        "such",
        "via",
        "non",
        "not",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "but",
        "our",
        "their",
        "its",
        "can",
        "may",
        "new",
        "two",
        "use",
        "all",
        "any",
    }
)


def _record_dedup_key(rec: PaperRecord) -> str:
    if rec.pmid and str(rec.pmid).strip():
        return f"pmid:{str(rec.pmid).strip()}"
    if rec.doi and str(rec.doi).strip():
        return f"doi:{str(rec.doi).strip().lower()}"
    t = re.sub(r"\s+", " ", (rec.title or "").lower()).strip()[:240]
    return f"title:{t}"


def _authors_from_delimited_string(author_string: str) -> List[str]:
    if not (author_string or "").strip():
        return []
    parts = [p.strip() for p in re.split(r"[;,]", author_string) if p.strip()]
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


async def _europepmc_search(client: httpx.AsyncClient, query: str, max_hits: int) -> List[Dict[str, Any]]:
    """Europe PMC REST search (cursorMark pagination)."""
    out: List[Dict[str, Any]] = []
    cursor_mark: Optional[str] = "*"
    max_hits = max(1, min(int(max_hits), 1000))
    while len(out) < max_hits:
        page_size = min(100, max_hits - len(out))
        params: Dict[str, Any] = {
            "query": query,
            "format": "json",
            "pageSize": page_size,
            "resultType": "core",
            "cursorMark": cursor_mark,
        }
        r = await client.get(_EUROPE_PMC_SEARCH, params=params, timeout=60.0)
        r.raise_for_status()
        data = r.json()
        batch = data.get("resultList", {}).get("result") or []
        if not batch:
            break
        out.extend(batch)
        next_mark = data.get("nextCursorMark")
        if not next_mark or next_mark == cursor_mark:
            break
        cursor_mark = next_mark
        if len(batch) < page_size:
            break
    return out[:max_hits]


def _paper_record_from_europepmc(hit: Dict[str, Any], seen_keys: Dict[str, int]) -> PaperRecord:
    title = (hit.get("title") or "").strip() or "Untitled"
    authors = _authors_from_delimited_string(str(hit.get("authorString") or ""))
    journal: Optional[str] = None
    ji = hit.get("journalInfo")
    if isinstance(ji, dict):
        jn = ji.get("journal")
        if isinstance(jn, dict):
            journal = (jn.get("title") or "").strip() or None
    year = str(hit.get("pubYear") or "").strip() or None
    if not year:
        year = _normalize_year(str(hit.get("firstPublicationDate") or ""))
    doi = (hit.get("doi") or "").strip() or None
    pmid = str(hit.get("pmid") or "").strip() or None
    pmcid_raw = hit.get("pmcid") or ""
    pmcid = str(pmcid_raw).strip() if pmcid_raw else None
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}" if pmcid.isdigit() else pmcid
    abstract = (hit.get("abstractText") or "").strip() or None
    url: Optional[str] = None
    if pmid:
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    elif doi:
        url = f"https://doi.org/{doi}"
    else:
        src = str(hit.get("source") or "MED")
        eid = str(hit.get("id") or "").strip()
        if eid:
            url = f"https://europepmc.org/article/{src}/{eid}"
    citekey = _build_citekey(
        authors=[a.split(" ", 1)[0] for a in authors] if authors else ["Anon"],
        year=year,
        journal=journal,
        seen=seen_keys,
    )
    return PaperRecord(
        citekey=citekey,
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        abstract=abstract,
        url=url,
    )


def _biorxiv_query_keywords(query: str) -> List[str]:
    # Unicode word chunks (English and other scripts); bioRxiv metadata is mostly English.
    raw_tokens = re.findall(r"[\w\-]{3,}", (query or ""), flags=re.UNICODE)
    out: List[str] = []
    for w in raw_tokens:
        wl = w.lower()
        if wl in _BIORXIV_STOPWORDS:
            continue
        if not re.search(r"[a-zA-Z]", w):
            continue
        out.append(wl)
    # de-dup preserve order
    seen: set[str] = set()
    dedup: List[str] = []
    for w in out:
        if w in seen:
            continue
        seen.add(w)
        dedup.append(w)
    return dedup[:24]


def _biorxiv_text_matches_keywords(haystack: str, keywords: List[str]) -> bool:
    if not keywords:
        return False
    low = haystack.lower()
    hits = sum(1 for k in keywords if k in low)
    need = 1 if len(keywords) <= 2 else 2
    return hits >= min(need, len(keywords))


async def _biorxiv_fetch_records(
    client: httpx.AsyncClient,
    query: str,
    max_hits: int,
    *,
    years_back: int = 3,
    seen_keys: Dict[str, int],
) -> List[PaperRecord]:
    """bioRxiv details API: date range + paginated cursor; filter rows by query keywords."""
    keywords = _biorxiv_query_keywords(query)
    if not keywords:
        return []
    max_hits = max(1, min(int(max_hits), 500))
    years_back = max(1, min(int(years_back), 10))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=365 * years_back)

    out: List[PaperRecord] = []
    cursor = 0
    max_pages = 10
    while len(out) < max_hits and cursor < max_pages:
        url = f"https://api.biorxiv.org/details/biorxiv/{start}/{end}/{cursor}"
        r = await client.get(url, timeout=90.0)
        r.raise_for_status()
        data = r.json()
        coll = data.get("collection") or []
        if not coll:
            break
        for item in coll:
            if len(out) >= max_hits:
                break
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            abstract = str(item.get("abstract") or "")
            if not _biorxiv_text_matches_keywords(f"{title} {abstract}", keywords):
                continue
            rec = _paper_record_from_biorxiv(item, seen_keys)
            out.append(rec)
        cursor += 1
        if len(coll) < 100:
            break
    return out


def _paper_record_from_biorxiv(item: Dict[str, Any], seen_keys: Dict[str, int]) -> PaperRecord:
    title = (item.get("title") or "").strip() or "Untitled"
    authors = _authors_from_delimited_string(str(item.get("authors") or "").replace(";", ","))
    year = _normalize_year(str(item.get("date") or "")) or str(item.get("date") or "")[:4]
    doi = str(item.get("doi") or "").strip()
    version = str(item.get("version") or "1").strip()
    abstract = (item.get("abstract") or "").strip() or None
    url = f"https://www.biorxiv.org/content/{doi}v{version}" if doi else None
    citekey = _build_citekey(
        authors=[a.split(" ", 1)[0] for a in authors] if authors else ["Anon"],
        year=year,
        journal="bioRxiv",
        seen=seen_keys,
    )
    return PaperRecord(
        citekey=citekey,
        title=title,
        authors=authors,
        year=year,
        journal="bioRxiv",
        doi=doi,
        pmid=None,
        pmcid=None,
        abstract=abstract,
        url=url,
        preprint_version=version,
    )


async def _download_biorxiv_pdf(
    client: httpx.AsyncClient, doi: str, version: str, out_path: Path
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Download bioRxiv full PDF for a 10.1101 DOI (best-effort)."""
    v = (version or "1").strip()
    clean = str(doi or "").strip()
    if not clean.startswith("10.1101"):
        return False, "not_a_biorxiv_doi", None
    url = f"https://www.biorxiv.org/content/{clean}v{v}.full.pdf"
    try:
        pr = await client.get(url, timeout=_PMC_LEGACY_PDF_TIMEOUT, follow_redirects=True)
        if pr.status_code >= 400:
            return False, f"HTTP {pr.status_code}", None
        if not _is_pdf_payload(pr.content, pr.headers.get("content-type"), url):
            return False, f"Downloaded content-type not pdf: {pr.headers.get('content-type')}", None
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pr.content)
        return True, None, None
    except Exception as exc:
        return False, str(exc), None


def _find_pmc_pdf_link(html: str, base_url: str) -> Optional[str]:
    # Heuristic: find the first href ending with .pdf.
    # PMC pages change; parsing HTML with regex is acceptable here because we only need the PDF link.
    for m in re.finditer(r'href="([^"]+?\.pdf[^"]*)"', html, flags=re.IGNORECASE):
        href = m.group(1)
        if href.startswith("#"):
            continue
        # Avoid supplemental PDFs or unrelated links if possible; prefer ones containing "/articles/" or "/bin/".
        if "pdf" in href.lower():
            return urljoin(base_url, href)
    # Sometimes the PDF is served via ?download=1 links.
    m2 = re.search(r'href="([^"]+download=1[^"]*)"', html, flags=re.IGNORECASE)
    if m2:
        return urljoin(base_url, m2.group(1))
    return None


def _is_pdf_payload(content: bytes, content_type: Optional[str], source_url: Optional[str] = None) -> bool:
    ctype = str(content_type or "").lower()
    url = str(source_url or "").lower()
    if content.startswith(b"%PDF-"):
        return True
    if "pdf" in ctype and not content.lstrip().startswith(b"<"):
        return True
    return url.endswith(".pdf") and content.startswith(b"%PDF-")


def _normalize_oa_href(href: str) -> str:
    normalized = str(href or "").strip()
    if normalized.startswith("ftp://ftp.ncbi.nlm.nih.gov/"):
        return "https://ftp.ncbi.nlm.nih.gov/" + normalized[len("ftp://ftp.ncbi.nlm.nih.gov/") :]
    return normalized


def _extract_text_from_oa_xml(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    texts: List[str] = []
    for chunk in root.itertext():
        normalized = re.sub(r"\s+", " ", str(chunk or "")).strip()
        if normalized:
            texts.append(normalized)
    return "\n".join(texts)


def _extract_oa_package_payload(package_bytes: bytes, out_path: Path) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    try:
        with tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:gz") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]

            pdf_member = next(
                (
                    member
                    for member in members
                    if member.name.lower().endswith(".pdf")
                ),
                None,
            )
            if pdf_member is not None:
                extracted = archive.extractfile(pdf_member)
                pdf_bytes = extracted.read() if extracted is not None else b""
                if _is_pdf_payload(pdf_bytes, "application/pdf", pdf_member.name):
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(pdf_bytes)
                    return out_path, None, None

            xml_member = next(
                (
                    member
                    for member in members
                    if member.name.lower().endswith(".nxml") or member.name.lower().endswith(".xml")
                ),
                None,
            )
            if xml_member is not None:
                extracted = archive.extractfile(xml_member)
                xml_bytes = extracted.read() if extracted is not None else b""
                full_text = _extract_text_from_oa_xml(xml_bytes)
                if full_text.strip():
                    return None, full_text, None
            return None, None, "OA package did not contain usable PDF or XML full text"
    except Exception as exc:
        return None, None, f"Failed to extract OA package: {exc}"


async def _download_pmc_oa_fulltext(
    client: httpx.AsyncClient, pmcid: str, out_path: Path
) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    oa_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}"
    response = await client.get(oa_url, timeout=_PMC_OA_UTILITY_TIMEOUT)
    if response.status_code >= 400:
        return None, None, f"OA utility HTTP {response.status_code}"

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        return None, None, f"OA utility XML parse failed: {exc}"

    links: List[Tuple[str, str]] = []
    for link_node in root.findall(".//record/link"):
        href = str(link_node.attrib.get("href") or "").strip()
        if not href:
            continue
        links.append((str(link_node.attrib.get("format") or "").strip().lower(), _normalize_oa_href(href)))

    if not links:
        return None, None, "OA utility returned no downloadable assets"

    pdf_links = [href for fmt, href in links if fmt == "pdf" or href.lower().endswith(".pdf")]
    for href in pdf_links:
        asset = await client.get(href, timeout=_PMC_OA_PDF_TIMEOUT, follow_redirects=True)
        if asset.status_code >= 400:
            continue
        if _is_pdf_payload(asset.content, asset.headers.get("content-type"), href):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(asset.content)
            return out_path, None, None

    archive_links = [href for fmt, href in links if fmt == "tgz" or href.lower().endswith((".tar.gz", ".tgz"))]
    for href in archive_links:
        asset = await client.get(href, timeout=_PMC_OA_ARCHIVE_TIMEOUT, follow_redirects=True)
        if asset.status_code >= 400:
            continue
        extracted_path, full_text, error = _extract_oa_package_payload(asset.content, out_path)
        if extracted_path is not None or (full_text and full_text.strip()):
            return extracted_path, full_text, None
        if error:
            logger.warning("OA package for %s unusable: %s", pmcid, error)

    return None, None, "OA utility assets did not yield usable PDF or XML full text"


def _fallback_pubmed_query(raw_query: str) -> Optional[str]:
    text = " ".join(str(raw_query or "").replace(",", " ").replace("，", " ").split()).strip()
    if not text:
        return None
    lowered = text.lower()
    topic_groups: List[str] = []

    if "pseudomonas" in lowered or "p. aeruginosa" in lowered:
        topic_groups.append("(Pseudomonas aeruginosa OR Pseudomonas)")
    if "phage" in lowered or "bacteriophage" in lowered:
        topic_groups.append("(phage OR bacteriophage)")

    topical_terms: List[str] = []
    term_mapping = [
        (("genom", "genomic", "genome"), "genomics"),
        (("host interaction", "host", "receptor", "adsorption"), "\"host interaction\""),
        (("therapy", "therapeutic", "application", "applications"), "therapy"),
        (("experimental models", "experimental", "model", "models"), "\"experimental models\""),
        (("biofilm",), "biofilm"),
        (("delivery", "hydrogel", "inhalable", "nanoparticle"), "delivery"),
    ]
    for aliases, value in term_mapping:
        if any(alias in lowered for alias in aliases):
            topical_terms.append(value)

    fallback_parts = list(topic_groups)
    if topical_terms:
        fallback_parts.append("(" + " OR ".join(_take_unique(topical_terms, limit=8)) + ")")

    if not fallback_parts:
        words = [
            token
            for token in re.findall(r"[A-Za-z0-9]+", text)
            if len(token) > 2
        ]
        if not words:
            return None
        fallback_parts.append("(" + " AND ".join(_take_unique(words, limit=6)) + ")")

    avoid = 'NOT ("phage display" OR "phage-displayed" OR "phage display library")'
    return " AND ".join(fallback_parts) + f" {avoid}"


async def _download_pmc_pdf(
    client: httpx.AsyncClient, pmcid: str, out_path: Path
) -> Tuple[bool, Optional[str], Optional[str]]:
    oa_pdf_path, oa_full_text, oa_error = await _download_pmc_oa_fulltext(client, pmcid, out_path)
    if oa_pdf_path is not None:
        return True, None, None
    if oa_full_text and oa_full_text.strip():
        return True, None, oa_full_text

    page_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
    r = await client.get(page_url, timeout=_PMC_ARTICLE_PAGE_TIMEOUT)
    if r.status_code >= 400:
        return False, oa_error or f"PMC page HTTP {r.status_code}", None
    pdf_url = _find_pmc_pdf_link(r.text or "", page_url)
    if not pdf_url:
        return False, oa_error or "PDF link not found on PMC page", None
    pr = await client.get(pdf_url, timeout=_PMC_LEGACY_PDF_TIMEOUT)
    if pr.status_code >= 400:
        return False, oa_error or f"PDF HTTP {pr.status_code}", None
    ctype = pr.headers.get("content-type")
    if not _is_pdf_payload(pr.content, ctype, pdf_url):
        logger.warning("Downloaded content-type not pdf: %s (%s)", ctype, pdf_url)
        return False, oa_error or f"Downloaded non-PDF payload from PMC page ({ctype or 'unknown'})", None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pr.content)
    return True, None, None


def _write_jsonl(path: Path, items: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _split_sentences(text: Optional[str]) -> List[str]:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?])\s+", raw)
    return [part.strip() for part in parts if part and part.strip()]


def _take_unique(items: Iterable[str], *, limit: int) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


def _extract_tag_values(text: str, patterns: List[Tuple[re.Pattern[str], str]], *, limit: int = 4) -> List[str]:
    values: List[str] = []
    for pattern, label in patterns:
        if pattern.search(text):
            values.append(label)
    return _take_unique(values, limit=limit)


def _extract_quantitative_findings(text: str, *, limit: int = 4) -> List[str]:
    findings: List[str] = []
    for sentence in _split_sentences(text):
        lowered = sentence.lower()
        if not re.search(r"\b\d+(?:\.\d+)?\b", sentence):
            continue
        if not any(hint in lowered for hint in _QUANTITATIVE_HINTS):
            continue
        findings.append(sentence)
    return _take_unique(findings, limit=limit)


def _extract_limitations(text: str, *, limit: int = 4) -> List[str]:
    findings: List[str] = []
    for sentence in _split_sentences(text):
        lowered = sentence.lower()
        if any(hint in lowered for hint in _LIMITATION_HINTS):
            findings.append(sentence)
    return _take_unique(findings, limit=limit)


def _supporting_snippets(*parts: Optional[str], limit: int = 4) -> List[str]:
    candidates: List[str] = []
    for part in parts:
        candidates.extend(_split_sentences(part))
    return _take_unique(candidates, limit=limit)


def _pick_study_type(text: str) -> str:
    for pattern, label in _STUDY_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return "unspecified"


def _infer_section_support(card: Dict[str, Any]) -> List[str]:
    sections = {"introduction", "method", "conclusion"}
    quantitative = card.get("quantitative_findings") or []
    limitations = card.get("limitations") or []
    models = card.get("model_system") or []
    mechanisms = card.get("receptor_mechanism_terms") or []
    interventions = card.get("intervention_delivery") or []
    study_type = str(card.get("study_type") or "").strip().lower()

    if models or study_type in {"clinical_trial", "clinical_case", "animal_model", "invertebrate_model", "in_vitro"}:
        sections.add("experiment")
    if quantitative or mechanisms or interventions:
        sections.add("result")
    if limitations or quantitative or mechanisms or interventions:
        sections.add("discussion")
    return [section for section in _CORE_REVIEW_SECTIONS if section in sections]


def _build_study_card(
    record: PaperRecord,
    *,
    pdf_path: Optional[Path] = None,
    full_text: Optional[str] = None,
) -> Dict[str, Any]:
    body_text = (full_text or "").strip()
    body_excerpt = body_text[:_MAX_FULLTEXT_CHARS] if body_text else ""
    source_text = "\n".join(
        item for item in (record.title, record.abstract or "", body_excerpt) if item
    ).strip()
    evidence_tier = "full_text" if body_excerpt else "abstract_only"
    quantitative_findings = _extract_quantitative_findings(source_text)
    limitations = _extract_limitations(source_text)
    card = {
        "citekey": record.citekey,
        "title": record.title,
        "authors": list(record.authors),
        "year": record.year,
        "journal": record.journal,
        "doi": record.doi,
        "pmid": record.pmid,
        "pmcid": record.pmcid,
        "url": record.url,
        "pdf_path": str(pdf_path.relative_to(_PROJECT_ROOT)) if isinstance(pdf_path, Path) and pdf_path.exists() else None,
        "evidence_tier": evidence_tier,
        "study_type": _pick_study_type(source_text),
        "model_system": _extract_tag_values(source_text, _MODEL_SYSTEM_PATTERNS),
        "bacterial_context": ["Pseudomonas aeruginosa"] if re.search(r"\bp\.?\s*aeruginosa\b|\bpseudomonas aeruginosa\b", source_text, re.I) else [],
        "phage_context": _extract_tag_values(source_text, [(re.compile(r"\bjumbo phage\b", re.I), "jumbo_phage"), (re.compile(r"\bcocktail\b", re.I), "cocktail"), (re.compile(r"\blytic\b", re.I), "lytic"), (re.compile(r"\btemperate\b", re.I), "temperate")]),
        "intervention_delivery": _extract_tag_values(source_text, _INTERVENTION_PATTERNS),
        "receptor_mechanism_terms": _extract_tag_values(source_text, _RECEPTOR_PATTERNS, limit=6),
        "quantitative_findings": quantitative_findings,
        "limitations": limitations,
        "supporting_snippets": _supporting_snippets(record.abstract, body_excerpt, limit=4),
    }
    card["section_support"] = _infer_section_support(card)
    return card


def _build_study_matrix_md(cards: List[Dict[str, Any]]) -> str:
    lines = [
        "# Study Matrix",
        "",
        "| Citekey | Year | Evidence | Study type | Model system | Quantitative findings | Supported sections |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for card in cards:
        model = ", ".join(card.get("model_system") or []) or "Not available"
        quant = "Yes" if card.get("quantitative_findings") else "No"
        supported = ", ".join(card.get("section_support") or []) or "Not available"
        lines.append(
            "| {citekey} | {year} | {tier} | {study_type} | {model} | {quant} | {supported} |".format(
                citekey=f"[@{card.get('citekey')}]",
                year=card.get("year") or "n.d.",
                tier=card.get("evidence_tier") or "unknown",
                study_type=card.get("study_type") or "unspecified",
                model=model,
                quant=quant,
                supported=supported,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _build_section_oriented_evidence_md(cards: List[Dict[str, Any]], coverage_report: Dict[str, Any], topic: str) -> str:
    section_titles = {
        "introduction": "Introduction and Background",
        "method": "Evidence Base and Review Method",
        "experiment": "Experimental Approaches and Model Systems",
        "result": "Key Findings and Comparative Signals",
        "discussion": "Limitations, Heterogeneity, and Translational Considerations",
        "conclusion": "Conclusion-Relevant Evidence",
    }
    lines = [
        "# Literature evidence inventory",
        "",
        f"Topic: {topic}",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Coverage summary",
        "",
        str(coverage_report.get("summary") or "Coverage report unavailable."),
        "",
    ]
    for section in _CORE_REVIEW_SECTIONS:
        lines.append(f"## {section_titles[section]}")
        lines.append("")
        cards_for_section = [
            card for card in cards if section in (card.get("section_support") or [])
        ]
        if not cards_for_section:
            lines.append("Not available")
            lines.append("")
            continue
        for card in cards_for_section[:12]:
            tier = card.get("evidence_tier") or "unknown"
            study_type = card.get("study_type") or "unspecified"
            models = ", ".join(card.get("model_system") or []) or "Not available"
            findings = "; ".join(card.get("quantitative_findings") or []) or "Not available"
            limits = "; ".join(card.get("limitations") or []) or "Not available"
            lines.append(
                f"- [@{card.get('citekey')}] {card.get('title')} ({card.get('year') or 'n.d.'}, {card.get('journal') or 'Unknown journal'})"
            )
            lines.append(f"  - Evidence tier: {tier}")
            lines.append(f"  - Study type: {study_type}")
            lines.append(f"  - Model system: {models}")
            lines.append(f"  - Quantitative findings: {findings}")
            lines.append(f"  - Limitations: {limits}")
        lines.append("")
    lines.append("## Notes")
    lines.append("- Citekeys are of the form `[@CITEKEY]`.")
    lines.append("- `full_text` records satisfy the strict evidence coverage gate; `abstract_only` records are supplemental.")
    lines.append("")
    return "\n".join(lines)


def _build_coverage_report(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    thresholds = dict(_COVERAGE_THRESHOLDS)
    section_support_counts = {
        section: len([card for card in cards if section in (card.get("section_support") or [])])
        for section in _CORE_REVIEW_SECTIONS
    }
    counts = {
        "total_studies": len(cards),
        "full_text_studies": len([card for card in cards if card.get("evidence_tier") == "full_text"]),
        "quantitative_studies": len([card for card in cards if card.get("quantitative_findings")]),
    }
    failures: List[str] = []
    if counts["total_studies"] < thresholds["min_total_studies"]:
        failures.append(
            f"only {counts['total_studies']} included studies; require at least {thresholds['min_total_studies']}"
        )
    if counts["full_text_studies"] < thresholds["min_full_text_studies"]:
        failures.append(
            f"only {counts['full_text_studies']} full-text studies; require at least {thresholds['min_full_text_studies']}"
        )
    if counts["quantitative_studies"] < thresholds["min_quantitative_studies"]:
        failures.append(
            f"only {counts['quantitative_studies']} studies with extractable quantitative findings; require at least {thresholds['min_quantitative_studies']}"
        )
    for section, support_count in section_support_counts.items():
        if support_count < thresholds["min_support_per_core_section"]:
            failures.append(
                f"{section} is supported by only {support_count} studies; require at least {thresholds['min_support_per_core_section']}"
            )
    passed = not failures
    if passed:
        summary = (
            "Evidence coverage passed: "
            f"{counts['total_studies']} studies included, "
            f"{counts['full_text_studies']} full-text studies, "
            f"{counts['quantitative_studies']} studies with quantitative findings, "
            "and all core review sections are supported."
        )
    else:
        summary = "Evidence coverage blocked: " + "; ".join(failures)
    return {
        "profile": "pi_ready_review",
        "pass": passed,
        "summary": summary,
        "thresholds": thresholds,
        "counts": counts,
        "section_support_counts": section_support_counts,
        "failures": failures,
        "included_citekeys": [card.get("citekey") for card in cards if card.get("citekey")],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_coverage_markdown(report: Dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    thresholds = report.get("thresholds") or {}
    section_support_counts = report.get("section_support_counts") or {}
    failures = report.get("failures") or []
    lines = [
        "# Evidence Coverage",
        "",
        f"Status: {'PASS' if report.get('pass') else 'BLOCKED'}",
        "",
        str(report.get("summary") or ""),
        "",
        "## Counts",
        "",
        f"- Total included studies: {counts.get('total_studies', 0)} (threshold: {thresholds.get('min_total_studies', 0)})",
        f"- Full-text studies: {counts.get('full_text_studies', 0)} (threshold: {thresholds.get('min_full_text_studies', 0)})",
        f"- Studies with quantitative findings: {counts.get('quantitative_studies', 0)} (threshold: {thresholds.get('min_quantitative_studies', 0)})",
        "",
        "## Core-section support",
        "",
    ]
    for section in _CORE_REVIEW_SECTIONS:
        lines.append(
            f"- {section}: {section_support_counts.get(section, 0)} supporting studies (threshold: {thresholds.get('min_support_per_core_section', 0)})"
        )
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    if failures:
        lines.extend(f"- {item}" for item in failures)
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _resolve_default_output_dir(*, session_id: Optional[str], timestamp: str) -> Path:
    if isinstance(session_id, str) and session_id.strip():
        from app.services.session_paths import get_session_tool_outputs_dir

        root = get_session_tool_outputs_dir(session_id.strip(), create=True)
        return (root / "literature_pipeline" / f"review_pack_{timestamp}").resolve()
    return (_RUNTIME_DIR / "literature" / f"review_pack_{timestamp}").resolve()


def _sanitize_relative_subpath(raw_path: Path) -> Path:
    safe_parts = [part for part in raw_path.parts if part not in ("", ".", "..")]
    if not safe_parts:
        return Path("review_pack")
    return Path(*safe_parts)


def _normalize_output_dir(raw_dir: Optional[str], *, default_dir: Path) -> Path:
    candidate = Path(raw_dir) if raw_dir else default_dir
    if candidate.is_absolute():
        return candidate.resolve()
    if raw_dir:
        return (_RUNTIME_DIR / "lit_reviews" / _sanitize_relative_subpath(candidate)).resolve()
    return (_PROJECT_ROOT / candidate).resolve()


def _is_within_root(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except ValueError:
        return False


def _build_evidence_md(records: List[PaperRecord], topic: str) -> str:
    # Simple grouping: host-interaction vs omics/database by keyword.
    host_kw = re.compile(r"\b(host|receptor|adsorption|infection|lysogen|temperate|CRISPR|immunity)\b", re.I)
    omics_kw = re.compile(r"\b(omics|metagenom|virom|database|catalog|atlas|pipeline|benchmark)\b", re.I)

    host: List[PaperRecord] = []
    omics: List[PaperRecord] = []
    other: List[PaperRecord] = []
    for r in records:
        text = f"{r.title}\n{r.abstract or ''}"
        if omics_kw.search(text):
            omics.append(r)
        elif host_kw.search(text):
            host.append(r)
        else:
            other.append(r)

    def _line(rec: PaperRecord) -> str:
        year = rec.year or "n.d."
        journal = rec.journal or "Unknown journal"
        pmcid = rec.pmcid or "-"
        doi = rec.doi or "-"
        pmid = rec.pmid or "-"
        # Include citekey in Markdown citekey form: [@key]
        return f"- [@{rec.citekey}] {rec.title} ({year}, {journal}) — DOI: {doi}; PMID: {pmid}; PMCID: {pmcid}"

    lines: List[str] = []
    lines.append(f"# Literature evidence inventory\n\nTopic: {topic}\n")
    lines.append(f"Generated at: {datetime.now(timezone.utc).isoformat()}\n")
    lines.append("## Section A: Phage–host interaction\n")
    lines.extend(_line(r) for r in host[:200])
    lines.append("\n## Section B: Phage omics / databases\n")
    lines.extend(_line(r) for r in omics[:200])
    if other:
        lines.append("\n## Section C: Other related\n")
        lines.extend(_line(r) for r in other[:200])
    lines.append("\n## Notes\n- Citekeys are of the form `[@CITEKEY]`.\n")
    return "\n".join(lines).strip() + "\n"


async def literature_pipeline_handler(
    query: str,
    *,
    max_results: int = 80,
    out_dir: Optional[str] = None,
    download_pdfs: bool = True,
    max_pdfs: int = 80,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    include_europepmc: bool = True,
    include_biorxiv: bool = True,
    biorxiv_years_back: int = 3,
) -> Dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        return {"tool": "literature_pipeline", "success": False, "error": "missing_query"}

    max_results = max(1, min(int(max_results), 500))
    max_pdfs = max(0, min(int(max_pdfs), 200))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # --- Unified output path: use PathRouter when task_id is available ---
    unified_output_dir: Optional[Path] = None
    if task_id is not None and session_id:
        from app.services.path_router import get_path_router
        path_router = get_path_router()
        unified_output_dir = path_router.get_task_output_dir(
            session_id, task_id, ancestor_chain, create=True
        )
        output_dir = unified_output_dir
    elif out_dir is None and session_id and not task_id:
        # Ad-hoc execution without task context → use PathRouter tmp
        from app.services.path_router import get_path_router
        path_router = get_path_router()
        unified_output_dir = path_router.get_tmp_output_dir(
            session_id, run_id=timestamp, create=True
        )
        output_dir = unified_output_dir
    else:
        try:
            default_dir = _resolve_default_output_dir(session_id=session_id, timestamp=timestamp)
        except Exception as exc:
            return {
                "tool": "literature_pipeline",
                "success": False,
                "error": f"session_output_dir_unavailable: {exc}",
            }
        output_dir = _normalize_output_dir(out_dir, default_dir=default_dir)
    # Ensure under project root for safety (skip for PathRouter-managed paths)
    if unified_output_dir is None and not _is_within_root(output_dir, _PROJECT_ROOT):
        return {"tool": "literature_pipeline", "success": False, "error": "out_dir_outside_project"}
    output_dir.mkdir(parents=True, exist_ok=True)

    library_path = output_dir / "library.jsonl"
    study_cards_path = output_dir / "study_cards.jsonl"
    coverage_report_path = output_dir / "coverage_report.json"
    bib_path = output_dir / "references.bib"
    evidence_path = output_dir / "evidence.md"
    pdf_dir = output_dir / "pdfs"
    docs_dir = output_dir / "docs"
    evidence_coverage_path = docs_dir / "evidence_coverage.md"
    study_matrix_path = docs_dir / "study_matrix.md"

    headers = {
        "accept": "application/json,text/xml,text/html,*/*",
        "user-agent": user_agent or "LLM-agent-literature-pipeline/1.0 (+https://example.local)",
    }

    progress_steps = [
        "query_pubmed",
        "fetch_metadata_pubmed",
    ]
    if include_europepmc:
        progress_steps.append("merge_europepmc")
    if include_biorxiv:
        progress_steps.append("merge_biorxiv")
    progress_steps.extend(
        [
            "build_bib",
            "download_pdfs",
            "write_evidence",
            "done",
        ]
    )

    def _bar(done: int, total: int, width: int = 24) -> str:
        total = max(1, total)
        frac = max(0.0, min(1.0, done / total))
        filled = int(round(frac * width))
        return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {int(frac*100)}%"

    errors: List[str] = []
    records: List[PaperRecord] = []
    downloaded: List[Dict[str, Any]] = []
    study_cards: List[Dict[str, Any]] = []

    # Prefer explicit proxy (tool-level) over environment proxy to avoid impacting other HTTP clients (e.g. LLM).
    effective_query = query
    fallback_query_used: Optional[str] = None
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        proxy=proxy,
        trust_env=False if proxy else True,
    ) as client:
        # 1) PubMed search
        try:
            pmids = await _pubmed_esearch(client, query, retmax=max_results)
            if not pmids:
                fallback_query = _fallback_pubmed_query(query)
                if fallback_query and fallback_query.strip() and fallback_query.strip() != query.strip():
                    retry_pmids = await _pubmed_esearch(client, fallback_query, retmax=max_results)
                    if retry_pmids:
                        pmids = retry_pmids
                        effective_query = fallback_query
                        fallback_query_used = fallback_query
        except Exception as exc:
            return {
                "tool": "literature_pipeline",
                "success": False,
                "error": f"pubmed_esearch_failed: {exc}",
            }

        # 2) Fetch metadata in batches
        seen_keys: Dict[str, int] = {}
        batch_size = 80
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i : i + batch_size]
            try:
                xml_text = await _pubmed_efetch_xml(client, batch)
                root = ET.fromstring(xml_text)
                for article in root.findall(".//PubmedArticle"):
                    title = _extract_title(article)
                    authors = _extract_authors(article)
                    journal, year = _extract_journal_year(article)
                    ids = _extract_article_ids(article)
                    abstract = _extract_abstract(article)
                    citekey = _build_citekey(
                        authors=[a.split(" ", 1)[0] for a in authors] if authors else ["Anon"],
                        year=year,
                        journal=journal,
                        seen=seen_keys,
                    )
                    url = None
                    if ids.get("pmid"):
                        url = f"https://pubmed.ncbi.nlm.nih.gov/{ids['pmid']}/"
                    rec = PaperRecord(
                        citekey=citekey,
                        title=title,
                        authors=authors,
                        year=year,
                        journal=journal,
                        doi=ids.get("doi"),
                        pmid=ids.get("pmid"),
                        pmcid=ids.get("pmcid"),
                        abstract=abstract,
                        url=url,
                    )
                    records.append(rec)
            except Exception as exc:
                errors.append(f"efetch_batch_{i//batch_size+1}: {exc}")

        dedup_keys: set[str] = {_record_dedup_key(r) for r in records}
        europepmc_added = 0
        biorxiv_added = 0

        if include_europepmc:
            try:
                epmc_hits = await _europepmc_search(client, effective_query, max_results)
                for hit in epmc_hits:
                    rec = _paper_record_from_europepmc(hit, seen_keys)
                    k = _record_dedup_key(rec)
                    if k in dedup_keys:
                        continue
                    dedup_keys.add(k)
                    records.append(rec)
                    europepmc_added += 1
            except Exception as exc:
                errors.append(f"europepmc_search: {exc}")

        if include_biorxiv:
            try:
                brx_records = await _biorxiv_fetch_records(
                    client,
                    effective_query,
                    max_results,
                    years_back=biorxiv_years_back,
                    seen_keys=seen_keys,
                )
                for rec in brx_records:
                    k = _record_dedup_key(rec)
                    if k in dedup_keys:
                        continue
                    dedup_keys.add(k)
                    records.append(rec)
                    biorxiv_added += 1
            except Exception as exc:
                errors.append(f"biorxiv_fetch: {exc}")

        if len(records) > max_results:
            records = records[:max_results]

        # 3) Write library.jsonl
        _write_jsonl(library_path, (r.to_json() for r in records))

        # 4) Write BibTeX
        bib_entries = "".join(_record_to_bibtex(r) for r in records)
        _write_text(bib_path, bib_entries)

        # 5) Download PDFs (PMC first, then bioRxiv)
        full_text_map: Dict[str, str] = {}
        if download_pdfs and max_pdfs > 0:
            pmc_candidates = [r for r in records if r.pmcid]
            brx_candidates = [
                r
                for r in records
                if (r.journal or "").lower() == "biorxiv"
                and r.doi
                and str(r.doi).strip().startswith("10.1101")
                and not r.pmcid
            ]
            candidates: List[PaperRecord] = []
            for r in pmc_candidates:
                if len(candidates) >= max_pdfs:
                    break
                candidates.append(r)
            for r in brx_candidates:
                if len(candidates) >= max_pdfs:
                    break
                candidates.append(r)
            semaphore = asyncio.Semaphore(_PMC_DOWNLOAD_CONCURRENCY)

            async def _download_candidate(rec: PaperRecord) -> Dict[str, Any]:
                out_pdf = pdf_dir / f"{rec.citekey}.pdf"
                try:
                    async with semaphore:
                        if rec.pmcid:
                            ok, err, downloaded_full_text = await _download_pmc_pdf(
                                client, rec.pmcid or "", out_pdf
                            )
                        elif (rec.journal or "").lower() == "biorxiv" and rec.doi:
                            ok, err, downloaded_full_text = await _download_biorxiv_pdf(
                                client,
                                rec.doi,
                                rec.preprint_version or "1",
                                out_pdf,
                            )
                        else:
                            ok, err, downloaded_full_text = False, "not_downloadable", None
                except Exception as exc:
                    ok = False
                    err = f"{type(exc).__name__}: {exc}"
                    downloaded_full_text = None
                return {
                    "citekey": rec.citekey,
                    "pmcid": rec.pmcid,
                    "ok": ok,
                    "path": str(out_pdf.relative_to(_PROJECT_ROOT)) if ok and out_pdf.exists() else None,
                    "error": err,
                    "full_text": downloaded_full_text,
                }

            download_results = await asyncio.gather(*(_download_candidate(rec) for rec in candidates))
            for item in download_results:
                downloaded_full_text = item.pop("full_text", None)
                if item.get("ok") and downloaded_full_text:
                    full_text_map[str(item.get("citekey"))] = str(downloaded_full_text)
                downloaded.append(item)
                if not item.get("ok") and item.get("error"):
                    errors.append(f"pdf:{item.get('citekey')}:{item.get('error')}")

        downloaded_map = {
            str(item.get("citekey")): item.get("path")
            for item in downloaded
            if item.get("ok") and item.get("citekey") and item.get("path")
        }

        for rec in records:
            pdf_path: Optional[Path] = None
            full_text: Optional[str] = full_text_map.get(rec.citekey)
            pdf_rel = downloaded_map.get(rec.citekey)
            if isinstance(pdf_rel, str) and pdf_rel.strip():
                candidate_pdf = (_PROJECT_ROOT / pdf_rel).resolve()
                if candidate_pdf.exists():
                    pdf_path = candidate_pdf
                    try:
                        from .document_reader import read_pdf

                        pdf_payload = await read_pdf(str(candidate_pdf))
                        if isinstance(pdf_payload, dict) and pdf_payload.get("success"):
                            text_value = str(pdf_payload.get("text") or "").strip()
                            if text_value:
                                full_text = text_value
                    except Exception as exc:
                        errors.append(f"pdf_parse:{rec.citekey}:{exc}")
            study_cards.append(
                _build_study_card(
                    rec,
                    pdf_path=pdf_path,
                    full_text=full_text,
                )
            )

        coverage_report = _build_coverage_report(study_cards)
        _write_jsonl(study_cards_path, study_cards)
        _write_text(coverage_report_path, json.dumps(coverage_report, ensure_ascii=False, indent=2))
        _write_text(evidence_path, _build_section_oriented_evidence_md(study_cards, coverage_report, topic=query))
        _write_text(evidence_coverage_path, _build_coverage_markdown(coverage_report))
        _write_text(study_matrix_path, _build_study_matrix_md(study_cards))

    result: Dict[str, Any] = {
        "tool": "literature_pipeline",
        "success": True,
        "query": query,
        "effective_query": effective_query,
        "fallback_query_used": fallback_query_used,
        "output_dir": str(output_dir.relative_to(_PROJECT_ROOT)),
        "evidence_coverage_passed": bool(coverage_report.get("pass")),
        "coverage_summary": str(coverage_report.get("summary") or "").strip(),
        "coverage_report_path": str(coverage_report_path.relative_to(_PROJECT_ROOT)),
        "outputs": {
            "library_jsonl": str(library_path.relative_to(_PROJECT_ROOT)),
            "study_cards_jsonl": str(study_cards_path.relative_to(_PROJECT_ROOT)),
            "coverage_report_json": str(coverage_report_path.relative_to(_PROJECT_ROOT)),
            "references_bib": str(bib_path.relative_to(_PROJECT_ROOT)),
            "evidence_md": str(evidence_path.relative_to(_PROJECT_ROOT)),
            "evidence_coverage_md": str(evidence_coverage_path.relative_to(_PROJECT_ROOT)),
            "study_matrix_md": str(study_matrix_path.relative_to(_PROJECT_ROOT)),
            "pdf_dir": str(pdf_dir.relative_to(_PROJECT_ROOT)),
        },
        "counts": {
            "pmids": len(pmids),
            "records": len(records),
            "pmcid_records": len([r for r in records if r.pmcid]),
            "europepmc_records_added": europepmc_added,
            "biorxiv_records_added": biorxiv_added,
            "pdf_attempted": len(downloaded),
            "pdf_downloaded": len([d for d in downloaded if d.get("ok")]),
            "study_cards": len(study_cards),
            "full_text_study_cards": len([card for card in study_cards if card.get("evidence_tier") == "full_text"]),
            "quantitative_study_cards": len([card for card in study_cards if card.get("quantitative_findings")]),
        },
        "sources": {
            "include_europepmc": include_europepmc,
            "include_biorxiv": include_biorxiv,
            "biorxiv_years_back": biorxiv_years_back,
        },
        "downloaded_pdfs": downloaded[:10],  # preview
        "errors": errors[:50] if errors else None,
        "progress_bar": _bar(done=len(progress_steps), total=len(progress_steps)),
        "progress_steps": progress_steps,
        "coverage_report": coverage_report,
    }

    # --- Unified output_location block (dual-write) ---
    if unified_output_dir:
        # Build file list relative to session root
        from app.services.session_paths import get_runtime_session_dir
        try:
            session_dir = get_runtime_session_dir(session_id, create=False) if session_id else None
        except ValueError:
            session_dir = None

        output_files: List[str] = []
        if session_dir and session_dir.exists():
            for f in sorted(output_dir.rglob("*")):
                if f.is_file():
                    try:
                        output_files.append(str(f.relative_to(session_dir)).replace("\\", "/"))
                    except ValueError:
                        output_files.append(str(f))

        result["output_location"] = {
            "type": "task" if task_id is not None else "tmp",
            "session_id": session_id,
            "task_id": task_id,
            "ancestor_chain": ancestor_chain,
            "base_dir": str(unified_output_dir),
            "files": output_files,
        }
        # Legacy fields (dual-write for backward compat)
        result["artifact_paths"] = [str(f.resolve()) for f in sorted(output_dir.rglob("*")) if f.is_file()]
        result["produced_files"] = result["artifact_paths"]
        result["session_artifact_paths"] = output_files

    return result


literature_pipeline_tool = {
    "name": "literature_pipeline",
    "description": (
        "Build a literature pack for a review: query PubMed, optionally merge Europe PMC + bioRxiv preprints, "
        "generate BibTeX citekeys, download OA PDFs (PMC and bioRxiv 10.1101/*), and write evidence inventory."
    ),
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (PubMed syntax for PubMed/Europe PMC; bioRxiv leg uses English keywords extracted from this string).",
            },
            "max_results": {
                "type": "integer",
                "default": 80,
                "description": "Max records after merging PubMed + optional Europe PMC + bioRxiv (<=500).",
            },
            "out_dir": {"type": "string", "description": "Output directory (project-relative preferred)."},
            "session_id": {
                "type": "string",
                "description": "Optional session id. When out_dir is omitted, outputs are written under the session tool_outputs directory.",
            },
            "download_pdfs": {
                "type": "boolean",
                "default": True,
                "description": "Download OA PDFs from PMC when PMCID exists, and bioRxiv full PDF when DOI is 10.1101/*.",
            },
            "max_pdfs": {"type": "integer", "default": 80, "description": "Max PDFs to download (PMC first, then bioRxiv)."},
            "include_europepmc": {
                "type": "boolean",
                "default": True,
                "description": "Merge additional hits from Europe PMC REST search (deduped vs PubMed).",
            },
            "include_biorxiv": {
                "type": "boolean",
                "default": True,
                "description": "Merge bioRxiv preprints from api.biorxiv.org (keyword-filtered; English tokens in query work best).",
            },
            "biorxiv_years_back": {
                "type": "integer",
                "default": 3,
                "description": "bioRxiv API date window length in years (1–10).",
            },
            "user_agent": {"type": "string", "description": "Optional User-Agent override."},
            "proxy": {
                "type": "string",
                "description": "Optional HTTP proxy URL (e.g. http://127.0.0.1:7897). If set, ignores env proxies.",
            },
        },
        "required": ["query"],
    },
    "handler": literature_pipeline_handler,
    "tags": ["pubmed", "pmc", "europepmc", "biorxiv", "bibtex", "review", "citations"],
    "examples": [
        "Build a pack for 'phage host interaction AND database' and save to runtime/literature/review_pack_x",
    ],
}
