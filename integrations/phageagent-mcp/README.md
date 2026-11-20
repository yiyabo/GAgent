[![mcptry](https://mcptry.com/badge/io.github.yiyabo/phageagent)](https://mcptry.com/servers/io.github.yiyabo/phageagent)

# PhageAgent MCP — client packages and directory listings

Source of the public repo https://github.com/yiyabo/phageagent-mcp (this folder becomes its root).

Remote MCP server: `https://phageagent.deepomics.org/mcp` (Streamable HTTP,
bearer token `gpsk_...`). Server code and access settings:
`app/services/phagescope_mcp/README.md`.

Every self-issued token gets all 17 tools: 11 read-only data tools
(`catalog_overview`, `search_phages`, `cohort_report`, `aggregate_phages`,
`get_phage`, `get_sequence`, `search_proteins`, `search_families`,
`family_members`, `query_features`, `get_structures`) and 6 analysis-task tools
that hand a request to PhageAgent and return its results. Data reads
are capped at 2 GB per token per day; analysis tasks have no quota during the
test phase.

## Access

Anyone can use it: sign up at https://phageagent.deepomics.org, then create a token yourself (see "Get a token" below). Questions: xxwang5587@gmail.com.

## Layout

| Path | For |
|---|---|
| `.claude-plugin/marketplace.json`, `claude/phageagent/` | Claude Code plugin marketplace; the token is a `sensitive` `userConfig` option |
| `.agents/plugins/marketplace.json`, `codex/phageagent/` | Codex plugin marketplace; the token comes from `PHAGEAGENT_MCP_TOKEN` |
| `hermes/manifest.yaml` | Draft entry for Hermes' curated catalog (`optional-mcps/phageagent/manifest.yaml` in NousResearch/hermes-agent) |
| `registry/server.json` | Official MCP Registry entry (`io.github.yiyabo/phageagent`) |
| `PRIVACY.md` | Privacy policy |

## Install

Sign up or sign in at https://phageagent.deepomics.org and `POST /phagescope-mcp/tokens {"name": "<client>"}`.

Claude Code:

```bash
claude plugin marketplace add yiyabo/phageagent-mcp
claude plugin install phageagent@phageagent
claude plugin configure phageagent@phageagent   # prompts for the token
```

Codex:

```bash
codex plugin marketplace add yiyabo/phageagent-mcp
codex plugin add phageagent@phageagent
export PHAGEAGENT_MCP_TOKEN=gpsk_...
```

Hermes (until the catalog entry is accepted):

```bash
hermes mcp add phageagent --url https://phageagent.deepomics.org/mcp \
  --header "Authorization: Bearer $PHAGEAGENT_MCP_TOKEN"
```

## Checks

- `claude plugin validate .` and `claude plugin validate ./claude/phageagent`
- `codex plugin marketplace add "$PWD" && codex plugin add phageagent@phageagent && codex mcp list`
- `HERMES_OPTIONAL_MCPS=<dir containing phageagent/manifest.yaml> hermes mcp catalog`
- `registry/server.json` against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
