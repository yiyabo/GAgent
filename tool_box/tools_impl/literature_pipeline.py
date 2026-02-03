"""
Literature Pipeline Tool
------------------------
Build a submission-grade literature pack for review writing:
- Query PubMed via NCBI E-utilities (ESearch + EFetch XML)
- Extract metadata (title/authors/year/journal/doi/pmid/pmcid/abstract)
- Generate stable citekeys and write:
  - library.jsonl (structured records)
  - references.bib (BibTeX with citekeys)
  - evidence.md (lightweight evidence inventory with citekeys)
- Download OA PDFs via PMC when PMCID is available (best-effort)

Design principles:
- No fabricated metadata: everything comes from NCBI/PMC responses.
- OA-only PDF download: we only download from PMC article pages we can access.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"


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

    def to_json(self) -> Dict[str, Any]:
        return {
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
    r = await client.get(url, params=params, timeout=40.0)
    r.raise_for_status()
    payload = r.json()
    ids = payload.get("esearchresult", {}).get("idlist", []) or []
    return [str(x) for x in ids if str(x).strip()]


async def _pubmed_efetch_xml(client: httpx.AsyncClient, pmids: List[str]) -> str:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": ",".join(pmids),
    }
    r = await client.get(url, params=params, timeout=60.0)
    r.raise_for_status()
    return r.text


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


async def _download_pmc_pdf(
    client: httpx.AsyncClient, pmcid: str, out_path: Path
) -> Tuple[bool, Optional[str]]:
    page_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
    r = await client.get(page_url, timeout=40.0)
    if r.status_code >= 400:
        return False, f"PMC page HTTP {r.status_code}"
    pdf_url = _find_pmc_pdf_link(r.text or "", page_url)
    if not pdf_url:
        return False, "PDF link not found on PMC page"
    pr = await client.get(pdf_url, timeout=80.0)
    if pr.status_code >= 400:
        return False, f"PDF HTTP {pr.status_code}"
    ctype = (pr.headers.get("content-type") or "").lower()
    if "pdf" not in ctype and not pdf_url.lower().endswith(".pdf"):
        # still write it, but flag
        logger.warning("Downloaded content-type not pdf: %s (%s)", ctype, pdf_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pr.content)
    return True, None


def _write_jsonl(path: Path, items: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
    max_pdfs: int = 30,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        return {"tool": "literature_pipeline", "success": False, "error": "missing_query"}

    max_results = max(1, min(int(max_results), 500))
    max_pdfs = max(0, min(int(max_pdfs), 200))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_dir = _RUNTIME_DIR / "literature" / f"review_pack_{timestamp}"
    output_dir = (Path(out_dir) if out_dir else default_dir)
    if not output_dir.is_absolute():
        output_dir = (_PROJECT_ROOT / output_dir).resolve()
    # Ensure under project root for safety
    if not str(output_dir).startswith(str(_PROJECT_ROOT)):
        return {"tool": "literature_pipeline", "success": False, "error": "out_dir_outside_project"}
    output_dir.mkdir(parents=True, exist_ok=True)

    library_path = output_dir / "library.jsonl"
    bib_path = output_dir / "references.bib"
    evidence_path = output_dir / "evidence.md"
    pdf_dir = output_dir / "pdfs"

    headers = {
        "accept": "application/json,text/xml,text/html,*/*",
        "user-agent": user_agent or "LLM-agent-literature-pipeline/1.0 (+https://example.local)",
    }

    progress_steps = [
        "query_pubmed",
        "fetch_metadata",
        "build_bib",
        "download_pdfs",
        "write_evidence",
        "done",
    ]

    def _bar(done: int, total: int, width: int = 24) -> str:
        total = max(1, total)
        frac = max(0.0, min(1.0, done / total))
        filled = int(round(frac * width))
        return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {int(frac*100)}%"

    errors: List[str] = []
    records: List[PaperRecord] = []
    downloaded: List[Dict[str, Any]] = []

    # Prefer explicit proxy (tool-level) over environment proxy to avoid impacting other HTTP clients (e.g. LLM).
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        proxy=proxy,
        trust_env=False if proxy else True,
    ) as client:
        # 1) PubMed search
        try:
            pmids = await _pubmed_esearch(client, query, retmax=max_results)
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

        # 3) Write library.jsonl
        _write_jsonl(library_path, (r.to_json() for r in records))

        # 4) Write BibTeX
        bib_entries = "".join(_record_to_bibtex(r) for r in records)
        _write_text(bib_path, bib_entries)

        # 5) Download PDFs (PMC only)
        if download_pdfs and max_pdfs > 0:
            candidates = [r for r in records if r.pmcid]
            for idx, rec in enumerate(candidates[:max_pdfs], start=1):
                out_pdf = pdf_dir / f"{rec.citekey}.pdf"
                ok, err = await _download_pmc_pdf(client, rec.pmcid or "", out_pdf)
                downloaded.append(
                    {
                        "citekey": rec.citekey,
                        "pmcid": rec.pmcid,
                        "ok": ok,
                        "path": str(out_pdf.relative_to(_PROJECT_ROOT)) if ok else None,
                        "error": err,
                    }
                )
                if not ok and err:
                    errors.append(f"pdf:{rec.citekey}:{err}")

        # 6) evidence.md
        _write_text(evidence_path, _build_evidence_md(records, topic=query))

    result: Dict[str, Any] = {
        "tool": "literature_pipeline",
        "success": True,
        "query": query,
        "output_dir": str(output_dir.relative_to(_PROJECT_ROOT)),
        "outputs": {
            "library_jsonl": str(library_path.relative_to(_PROJECT_ROOT)),
            "references_bib": str(bib_path.relative_to(_PROJECT_ROOT)),
            "evidence_md": str(evidence_path.relative_to(_PROJECT_ROOT)),
            "pdf_dir": str(pdf_dir.relative_to(_PROJECT_ROOT)),
        },
        "counts": {
            "pmids": len(pmids),
            "records": len(records),
            "pmcid_records": len([r for r in records if r.pmcid]),
            "pdf_attempted": len(downloaded),
            "pdf_downloaded": len([d for d in downloaded if d.get("ok")]),
        },
        "downloaded_pdfs": downloaded[:10],  # preview
        "errors": errors[:50] if errors else None,
        "progress_bar": _bar(done=len(progress_steps), total=len(progress_steps)),
        "progress_steps": progress_steps,
    }
    return result


literature_pipeline_tool = {
    "name": "literature_pipeline",
    "description": (
        "Build a literature pack for a review: query PubMed, fetch metadata, generate BibTeX citekeys, "
        "download OA PDFs from PMC when available, and write evidence inventory."
    ),
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "PubMed query term (supports boolean operators)."},
            "max_results": {"type": "integer", "default": 80, "description": "Max PubMed results to fetch (<=500)."},
            "out_dir": {"type": "string", "description": "Output directory (project-relative preferred)."},
            "download_pdfs": {"type": "boolean", "default": True, "description": "Download OA PDFs from PMC when PMCID exists."},
            "max_pdfs": {"type": "integer", "default": 30, "description": "Max PDFs to download (PMC only)."},
            "user_agent": {"type": "string", "description": "Optional User-Agent override."},
            "proxy": {
                "type": "string",
                "description": "Optional HTTP proxy URL (e.g. http://127.0.0.1:7897). If set, ignores env proxies.",
            },
        },
        "required": ["query"],
    },
    "handler": literature_pipeline_handler,
    "tags": ["pubmed", "pmc", "bibtex", "review", "citations"],
    "examples": [
        "Build a pack for 'phage host interaction AND database' and save to runtime/literature/review_pack_x",
    ],
}

