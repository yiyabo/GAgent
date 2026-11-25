[![mcptry](https://mcptry.com/badge/io.github.yiyabo/phageagent)](https://mcptry.com/servers/io.github.yiyabo/phageagent)

# PhageAgent MCP

Remote MCP server for the PhageScope phage database (about 2.6M phage genomes,
131M proteins, 12.6M protein families) plus analysis tasks run by PhageAgent.

- Endpoint: `https://phageagent.deepomics.org/mcp` (Streamable HTTP)
- Auth: `Authorization: Bearer gpsk_...`, a personal token you create yourself
- Access: anyone can sign up. Questions: xxwang5587@gmail.com

## Introduction website (Chinese)

The responsive introduction site is in [`docs/`](docs/index.html), with a
[shareable Chinese introduction](docs/intro-zh.md). It is a dependency-free
static site: preview with `python -m http.server 8080 --directory docs`.
It never receives tokens or calls the MCP service. After publication approval,
GitHub Pages can serve the public repository's `main` branch, `/docs` folder;
adding these files does not enable Pages or deploy anything.

## 1. Get a token

1. Open https://phageagent.deepomics.org/register and create an account
   (email + password), or sign in at https://phageagent.deepomics.org/login.
2. Click **Extensions** (扩展) in the top bar, or go to
   https://phageagent.deepomics.org/extensions.
3. Open the **External access** (外部接入) tab.
4. Under **Your tokens**, type a name such as `claude-code` and click
   **Create token** (创建令牌).
5. Copy the `gpsk_...` value right away: it is shown only once. Tokens expire
   after 90 days; revoke one from the same table.

Keep it in an environment variable rather than in a config file:

```bash
export PHAGEAGENT_MCP_TOKEN=gpsk_...
```

Scripts can also create tokens over HTTP with a signed-in session:
`POST /phagescope-mcp/tokens {"name": "<client>"}` (list: `GET`, revoke:
`DELETE /phagescope-mcp/tokens/{id}`). Without a session it answers 401.

## 2. Connect a client

Any client with a JSON `mcpServers` file and HTTP support (Cursor, WorkBuddy,
Hermes, Devin and others):

```json
{
  "mcpServers": {
    "phageagent": {
      "url": "https://phageagent.deepomics.org/mcp",
      "headers": { "Authorization": "Bearer ${PHAGEAGENT_MCP_TOKEN}" }
    }
  }
}
```

Claude Code (plugin; prompts for the token):

```bash
claude plugin marketplace add yiyabo/phageagent-mcp
claude plugin install phageagent@phageagent
claude plugin configure phageagent@phageagent
```

Codex (plugin; reads `PHAGEAGENT_MCP_TOKEN`):

```bash
codex plugin marketplace add yiyabo/phageagent-mcp
codex plugin add phageagent@phageagent
```

Hermes (until the catalog entry is accepted):

```bash
hermes mcp add phageagent --url https://phageagent.deepomics.org/mcp \
  --header "Authorization: Bearer $PHAGEAGENT_MCP_TOKEN"
```

stdio-only clients can bridge with
`npx -y mcp-remote https://phageagent.deepomics.org/mcp --header "Authorization: Bearer ${PHAGEAGENT_MCP_TOKEN}"`.

## 3. First query

Ask your agent: *"Use PhageAgent to look up phage NC_001416.1."* It calls
`get_phage` with `{"accession": "NC_001416.1"}`. Without a client, the same
call is:

```bash
curl -s https://phageagent.deepomics.org/mcp \
  -H "Authorization: Bearer $PHAGEAGENT_MCP_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"get_phage","arguments":{"accession":"NC_001416.1"}}}'
```

Expected result (Enterobacteria phage lambda, 48,502 bp; the JSON arrives as
one text block, shortened here):

```json
{
  "tool": "get_phage",
  "data": {
    "id": 13, "Acession_ID": "NC_001416.1", "dataset": "RefSeq",
    "length": 48502, "host": "Escherichia coli", "lifestyle": "temperate",
    "completeness": "High-quality", "taxonomy": "Caudoviricetes", "protein_count": 73,
    "host_lineage": {"Family": "Enterobacteriaceae", "Genus": "Escherichia", "...": "..."}
  },
  "provenance": {
    "source": "PhageScope via the GAgent read-only data service",
    "snapshot": {"checked_on": "2026-09-11"},
    "retrieved_at": "..."
  },
  "limits": {"returned_rows": null, "truncated": false},
  "notes": ["A missing or null field means the value is unknown, not zero or absent."]
}
```

A null field means unknown, never zero. `401` means the token is missing,
mistyped, revoked or expired; its body links to [Get a token](#1-get-a-token)
in `error.help_url`. A failed tool call returns `isError: true` with
`{"error": {"code", "message"}}`; bad or missing arguments always use the code
`invalid_arguments`. `429` (with `Retry-After`) means too many requests per
token or per client IP.

## Tools

Every token gets all 21 tools. Data reads are capped at 2 GB per token per
day; analysis tasks have no quota during the test phase.

<!-- tools:begin -->
| Tool | What it does |
|---|---|
| `catalog_overview` | Catalog-wide counts and the dataset list. Start here. |
| `search_phages` | Phages matching filters (host, taxonomy, lifestyle, dataset, length, GC...), paged |
| `cohort_report` | Size, composition breakdowns and sample rows of a filtered cohort |
| `aggregate_phages` | Phage counts grouped by 1-3 dimensions |
| `get_phage` | Metadata of one phage: taxonomy, host lineage, lifestyle, quality, cluster |
| `get_sequence` | Genome sequence of one phage, capped at `max_bases` |
| `search_proteins` | Proteins of one phage (by product text or function class), or proteins by id |
| `search_families` | Protein families by keyword, member protein id or family id |
| `family_members` | Member proteins of one family, paged |
| `query_features` | Annotated tRNA, CRISPR, anti-CRISPR, terminator, virulence, AMR, transmembrane features |
| `get_structures` | Structure availability and links for protein ids |
| `similarity_search` | MMseqs2 search of 1-50 protein sequences against PhageScope proteins |
| `get_similarity_result` | Status and paged hits of a `similarity_search` by its `search_id` |
| `list_local_tools` | Docker tools and ready-made workflows for analysis on your own machine |
| `plan_local_analysis` | `docker pull`, database setup and `docker run` commands for a workflow or chosen steps, sized to your machine |
| `submit_analysis` | Hand an analysis request to PhageAgent; returns a task id |
| `get_analysis_status` | Status and recent progress of a task |
| `get_analysis_result` | Summary, deliverables and provenance of a finished task |
| `reply_analysis` | Continue a task with a follow-up message |
| `cancel_analysis` | Stop a working task |
| `list_analyses` | This token's recent tasks, newest first |
<!-- tools:end -->

The first 13 are read-only data tools; the last 6 run analysis tasks. The
server's `tools/list` is the source of truth; `scripts/check_tools_doc.py`
fails a release when this table and `tools/list` differ.

## Layout

| Path | For |
|---|---|
| `.claude-plugin/marketplace.json`, `claude/phageagent/` | Claude Code plugin marketplace; the token is a `sensitive` `userConfig` option |
| `.agents/plugins/marketplace.json`, `codex/phageagent/` | Codex plugin marketplace; the token comes from `PHAGEAGENT_MCP_TOKEN` |
| `hermes/manifest.yaml` | Draft entry for Hermes' curated catalog (`optional-mcps/phageagent/manifest.yaml` in NousResearch/hermes-agent) |
| `registry/server.json` | Official MCP Registry entry (`io.github.yiyabo/phageagent`) |
| `scripts/check_tools_doc.py` | Compares the Tools table with the live `tools/list` |
| `PRIVACY.md` | Privacy policy |

This repository is published from `integrations/phageagent-mcp` in
[yiyabo/GAgent](https://github.com/yiyabo/GAgent); server code and access
settings are in `app/services/phagescope_mcp/README.md` there.

## Checks

- Docs vs server, on every release and daily in CI:
  `python scripts/check_tools_doc.py --url https://phageagent.deepomics.org/mcp README.md`
- `claude plugin validate .` and `claude plugin validate ./claude/phageagent`
- `codex plugin marketplace add "$PWD" && codex plugin add phageagent@phageagent && codex mcp list`
- `HERMES_OPTIONAL_MCPS=<dir containing phageagent/manifest.yaml> hermes mcp catalog`
- `registry/server.json` against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
