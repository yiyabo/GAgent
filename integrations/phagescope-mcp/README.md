# PhageScope MCP — client packages and directory listings

Source of the public repo https://github.com/yiyabo/phagescope-mcp (this folder becomes its root).

Remote MCP server: `https://phageagent.deepomics.org/mcp` (Streamable HTTP,
bearer token `gpsk_...`). Server code and access settings:
`app/services/phagescope_mcp/README.md`.

Every self-issued token gets all 17 tools: 11 read-only data tools
(`catalog_overview`, `search_phages`, `cohort_report`, `aggregate_phages`,
`get_phage`, `get_sequence`, `search_proteins`, `search_families`,
`family_members`, `query_features`, `get_structures`) and 6 analysis-task tools
that hand a request to the PhageScope agent and return its results. Data reads
are capped at 2 GB per token per day; analysis tasks have no quota during the
test phase.

## Access

Anyone can use it: sign up at https://phageagent.deepomics.org, then create a token yourself (see "Get a token" below). Questions: xxwang5587@gmail.com.

## Layout

| Path | For |
|---|---|
| `.claude-plugin/marketplace.json`, `claude/phagescope/` | Claude Code plugin marketplace; the token is a `sensitive` `userConfig` option |
| `.agents/plugins/marketplace.json`, `codex/phagescope/` | Codex plugin marketplace; the token comes from `PHAGESCOPE_MCP_TOKEN` |
| `hermes/manifest.yaml` | Draft entry for Hermes' curated catalog (`optional-mcps/phagescope/manifest.yaml` in NousResearch/hermes-agent) |
| `registry/server.json` | Official MCP Registry entry (`io.github.yiyabo/phagescope`) |
| `PRIVACY.md` | Privacy policy |

## Install

Sign up or sign in at https://phageagent.deepomics.org and `POST /phagescope-mcp/tokens {"name": "<client>"}`.

Claude Code:

```bash
claude plugin marketplace add yiyabo/phagescope-mcp
claude plugin install phagescope@phagescope
claude plugin configure phagescope@phagescope   # prompts for the token
```

Codex:

```bash
codex plugin marketplace add yiyabo/phagescope-mcp
codex plugin add phagescope@phagescope
export PHAGESCOPE_MCP_TOKEN=gpsk_...
```

Hermes (until the catalog entry is accepted):

```bash
hermes mcp add phagescope --url https://phageagent.deepomics.org/mcp \
  --header "Authorization: Bearer $PHAGESCOPE_MCP_TOKEN"
```

## Checks

- `claude plugin validate .` and `claude plugin validate ./claude/phagescope`
- `codex plugin marketplace add "$PWD" && codex plugin add phagescope@phagescope && codex mcp list`
- `HERMES_OPTIONAL_MCPS=<dir containing phagescope/manifest.yaml> hermes mcp catalog`
- `registry/server.json` against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
