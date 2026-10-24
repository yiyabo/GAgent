"""ncbi_search: Entrez search, summaries and taxonomy over the shared E-utilities client (mocked NCBI)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result
from tool_box.tools_impl import ncbi_eutils, ncbi_search

LAMBDA = {
    "uid": "9626243", "caption": "NC_001416", "accessionversion": "NC_001416.1",
    "title": "Enterobacteria phage lambda, complete genome", "organism": "Escherichia phage Lambda",
    "taxid": 2681611, "slen": 48502, "moltype": "dna", "topology": "linear", "completeness": "complete",
    "sourcedb": "refseq", "updatedate": "2023/01/11", "statistics": [{"type": "all"}],
}
T7 = dict(LAMBDA, uid="9627425", caption="NC_001604", accessionversion="NC_001604.1",
          title="Enterobacteria phage T7, complete genome", organism="Escherichia phage T7", slen=39937)
ASSEMBLY = {
    "uid": "4689368", "assemblyaccession": "GCF_000840245.1", "assemblyname": "ViralProj14204",
    "organism": "Escherichia phage Lambda (viruses)", "speciesname": "Lambdavirus lambda", "taxid": "2681611",
    "assemblystatus": "Complete Genome", "refseq_category": "na", "asmreleasedate_genbank": "2017/07/19 00:00",
    "asmreleasedate_refseq": "1/01/01 00:00", "contign50": 0,
    "ftppath_refseq": "ftp://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/840/245/GCF_000840245.1_ViralProj14204",
    "ftppath_genbank": "",
}
TAXON_XML = """<?xml version="1.0" ?>
<!DOCTYPE TaxaSet PUBLIC "-//NLM//DTD Taxon, 14th January 2002//EN" "https://www.ncbi.nlm.nih.gov/entrez/query/DTD/taxon.dtd">
<TaxaSet><Taxon>
  <TaxId>10710</TaxId><ScientificName>Lambdavirus lambda</ScientificName>
  <OtherNames><EquivalentName>Escherichia virus Lambda</EquivalentName></OtherNames>
  <ParentTaxId>186765</ParentTaxId><Rank>species</Rank><Division>Phages</Division>
  <GeneticCode><GCId>11</GCId><GCName>Bacterial</GCName></GeneticCode>
  <Lineage>Viruses; Duplodnaviria; Heunggongvirae; Uroviricota; Caudoviricetes; Lambdavirus</Lineage>
  <LineageEx>
    <Taxon><TaxId>10239</TaxId><ScientificName>Viruses</ScientificName><Rank>superkingdom</Rank></Taxon>
    <Taxon><TaxId>2731341</TaxId><ScientificName>Duplodnaviria</ScientificName><Rank>realm</Rank></Taxon>
    <Taxon><TaxId>2788787</TaxId><ScientificName>unclassified thing</ScientificName><Rank>no rank</Rank></Taxon>
    <Taxon><TaxId>2731619</TaxId><ScientificName>Caudoviricetes</ScientificName><Rank>class</Rank></Taxon>
  </LineageEx>
</Taxon></TaxaSet>"""


def _summary(*records):
    result = {"uids": [r["uid"] for r in records]}
    result.update({r["uid"]: r for r in records})
    return {"result": result}


class FakeNcbi:
    """Routes E-utility requests to canned replies and records what was asked."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        self.owners = []

    def params(self, request):
        if request.method == "POST":
            return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return dict(request.url.params)

    def __call__(self, request):
        self.requests.append(request)
        self.owners.append(ncbi_eutils._current_owner.get())
        endpoint = request.url.path.rsplit("/", 1)[-1]
        reply = self.routes[endpoint]
        reply = reply(self.params(request)) if callable(reply) else reply
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, str):
            return httpx.Response(200, text=reply)
        return httpx.Response(200, json=reply)


@pytest.fixture()
def fake(monkeypatch):
    async def _no_wait():
        return None

    monkeypatch.setattr(ncbi_eutils, "wait_for_slot", _no_wait)
    monkeypatch.setattr(ncbi_eutils, "_retry_delay", lambda attempt, retry_after: 0.0)

    def install(routes):
        server = FakeNcbi(routes)
        monkeypatch.setattr(ncbi_search, "_new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(server)))
        return server

    return install


async def test_search_returns_summaries_and_a_paging_offset(fake):
    server = fake({
        "esearch.fcgi": {"esearchresult": {"count": "3", "idlist": ["9626243"], "querytranslation": "\"Escherichia phage Lambda\"[Organism]"}},
        "esummary.fcgi": _summary(LAMBDA),
    })
    result = await ncbi_search.ncbi_search_handler(action="search", term="Escherichia phage lambda[orgn]", retmax=1, session_id="s1")
    assert result["success"] is True and result["count"] == 3 and result["returned"] == 1
    record = result["records"][0]
    assert record["accession"] == "NC_001416.1" and record["length"] == 48502 and record["taxid"] == 2681611
    assert "statistics" not in record
    assert result["next_retstart"] == 1
    assert result["query_translation"].startswith('"Escherichia phage Lambda"')
    esearch, esummary = server.requests
    assert esearch.url.params["retmax"] == "1" and esearch.url.params["db"] == "nuccore"
    assert esummary.url.params["id"] == "9626243"
    assert server.owners == ["s1", "s1"]


async def test_a_search_without_hits_reports_why_and_skips_esummary(fake):
    server = fake({
        "esearch.fcgi": {"esearchresult": {
            "count": "0", "idlist": [], "querytranslation": "zzz[orgn]",
            "errorlist": {"phrasesnotfound": ["zzz[orgn]"], "fieldsnotfound": []},
            "warninglist": {"phrasesignored": [], "quotedphrasesnotfound": [], "outputmessages": ["No items found."]},
        }},
    })
    result = await ncbi_search.ncbi_search_handler(action="search", term="zzz[orgn]")
    assert result["success"] is True and result["count"] == 0 and result["records"] == []
    assert "phrase not found: zzz[orgn]" in result["notes"] and "No items found." in result["notes"]
    assert "next_retstart" not in result
    assert len(server.requests) == 1


async def test_assembly_accessions_are_resolved_and_ftp_links_become_https(fake):
    server = fake({
        "esearch.fcgi": lambda params: {"esearchresult": {"count": "1", "idlist": ["4689368"], "term": params["term"]}},
        "esummary.fcgi": _summary(ASSEMBLY),
    })
    result = await ncbi_search.ncbi_search_handler(action="summary", db="assembly", ids=["GCF_000840245.1"])
    assert server.requests[0].url.params["term"] == "GCF_000840245.1[Assembly Accession]"
    row = result["records"][0]
    assert row["accession"] == "GCF_000840245.1" and row["status"] == "Complete Genome"
    assert row["ftp_refseq"].startswith("https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/")
    assert row["released"] == "2017/07/19 00:00"
    assert "refseq_category" not in row and "contig_n50" not in row and "ftp_genbank" not in row
    assert "missing" not in result


async def test_summary_lists_ids_ncbi_did_not_return(fake):
    fake({"esummary.fcgi": _summary(LAMBDA, T7)})
    result = await ncbi_search.ncbi_search_handler(action="summary", ids="NC_001416.1, NC_001604 , NC_999999.1")
    assert [r["accession"] for r in result["records"]] == ["NC_001416.1", "NC_001604.1"]
    assert result["missing"] == ["NC_999999.1"]
    assert result["requested"] == 3


async def test_large_id_lists_are_posted(fake):
    server = fake({"esummary.fcgi": _summary(LAMBDA)})
    ids = [str(100000 + n) for n in range(ncbi_search.POST_ABOVE_IDS + 1)]
    await ncbi_search.ncbi_search_handler(action="summary", ids=ids)
    request = server.requests[0]
    assert request.method == "POST"
    assert server.params(request)["id"].count(",") == len(ids) - 1


async def test_taxonomy_by_name_gives_ranked_lineage(fake):
    fake({
        "esearch.fcgi": {"esearchresult": {"count": "1", "idlist": ["10710"]}},
        "efetch.fcgi": TAXON_XML,
    })
    result = await ncbi_search.ncbi_search_handler(action="taxonomy", term="Lambdavirus lambda")
    taxon = result["taxa"][0]
    assert taxon["taxid"] == "10710" and taxon["rank"] == "species" and taxon["genetic_code"] == "11"
    assert [step["rank"] for step in taxon["ranked_lineage"]] == ["superkingdom", "realm", "class"]
    assert taxon["other_names"] == ["Escherichia virus Lambda"]
    assert taxon["lineage"].startswith("Viruses; Duplodnaviria")


async def test_taxonomy_by_taxid_reports_unknown_ids(fake):
    fake({"efetch.fcgi": TAXON_XML})
    result = await ncbi_search.ncbi_search_handler(action="taxonomy", ids=["10710", "424242"])
    assert result["returned"] == 1 and result["missing"] == ["424242"]


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"action": "browse"}, "invalid_action"),
        ({"action": "search", "db": "omim", "term": "x"}, "invalid_db"),
        ({"action": "search"}, "missing_term"),
        ({"action": "search", "term": "x" * 1001}, "invalid_term"),
        ({"action": "search", "term": "a\nb"}, "invalid_term"),
        ({"action": "search", "term": "x", "retstart": 10_000}, "retstart_out_of_range"),
        ({"action": "summary"}, "missing_ids"),
        ({"action": "summary", "ids": ["NC_001416.1&db=pubmed"]}, "invalid_ids"),
        ({"action": "summary", "ids": [str(n) for n in range(201)]}, "too_many_ids"),
        ({"action": "taxonomy", "ids": ["Lambda"]}, "invalid_ids"),
        ({"action": "taxonomy"}, "missing_term"),
    ],
)
async def test_bad_input_is_refused_before_any_request(fake, kwargs, code):
    server = fake({})
    result = await ncbi_search.ncbi_search_handler(**kwargs)
    assert result["success"] is False and result["error_code"] == code
    assert server.requests == []


async def test_persistent_rate_limiting_comes_back_as_a_clear_error(fake):
    fake({"esearch.fcgi": httpx.Response(429, json={"error": "API rate limit exceeded"})})
    result = await ncbi_search.ncbi_search_handler(action="search", term="phage")
    assert result["success"] is False and result["error_code"] == "ncbi_rate_limited"
    assert "already retried" in result["retry_hint"]


async def test_results_survive_sanitizing_and_read_well_in_summaries(fake):
    fake({
        "esearch.fcgi": {"esearchresult": {"count": "2", "idlist": ["9626243", "9627425"]}},
        "esummary.fcgi": _summary(LAMBDA, T7),
    })
    result = await ncbi_search.ncbi_search_handler(action="search", term="phage[orgn]")
    sanitized = sanitize_tool_result("ncbi_search", result)
    assert [r["accession"] for r in sanitized["records"]] == ["NC_001416.1", "NC_001604.1"]
    assert sanitized["count"] == 2
    assert summarize_tool_result("ncbi_search", result) == "NCBI search on nuccore; 2 matches; 2 records returned."


def test_schema_and_registry_agree():
    from tool_box.tool_registry import get_tool_orchestration_metadata

    schema = ncbi_search.ncbi_search_tool["parameters_schema"]
    assert schema["properties"]["action"]["enum"] == list(ncbi_search.ACTIONS)
    assert schema["properties"]["retmax"]["maximum"] == ncbi_search.MAX_RETMAX
    meta = get_tool_orchestration_metadata("ncbi_search")
    assert meta["is_read_only"] is True and meta["is_concurrent_safe"] is True
    json.dumps(schema)
