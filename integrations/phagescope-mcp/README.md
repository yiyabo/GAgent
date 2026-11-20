# PhageScope MCP — client packages and directory listings

Remote MCP server: `https://phageagent.deepomics.org/mcp` (Streamable HTTP,
bearer token `gpsk_...`). Server code and access settings:
`app/services/phagescope_mcp/README.md`.

Public scope is the 11 read-only tools (`catalog_overview`, `search_phages`,
`cohort_report`, `aggregate_phages`, `get_phage`, `get_sequence`,
`search_proteins`, `search_families`, `family_members`, `query_features`,
`get_structures`). The 6 analysis-task tools are only listed for tokens with
the `analysis:submit` scope (invite only); these packages do not mention them.

The marketplaces live at the GAgent repo root, so `yiyabo/GAgent` is the marketplace source.

## Layout

| Path | For |
|---|---|
| repo-root `.claude-plugin/marketplace.json`, `claude/phagescope/` | Claude Code plugin marketplace; the token is a `sensitive` `userConfig` option |
| repo-root `.agents/plugins/marketplace.json`, `codex/phagescope/` | Codex plugin marketplace; the token comes from `PHAGESCOPE_MCP_TOKEN` |
| `hermes/manifest.yaml` | Draft entry for Hermes' curated catalog (`optional-mcps/phagescope/manifest.yaml` in NousResearch/hermes-agent) |
| `registry/server.json` | Official MCP Registry entry (`io.github.yiyabo/phagescope`) |
| `PRIVACY.md` | Privacy policy |

## Install

Get a token first: sign in at https://phageagent.deepomics.org (account must be
allowlisted) and `POST /phagescope-mcp/tokens {"name": "<client>"}`.

Claude Code:

```bash
claude plugin marketplace add yiyabo/GAgent
claude plugin install phagescope@phagescope
claude plugin configure phagescope@phagescope   # prompts for the token
```

Codex:

```bash
codex plugin marketplace add yiyabo/GAgent
codex plugin add phagescope@phagescope
export PHAGESCOPE_MCP_TOKEN=gpsk_...
```

Hermes (until the catalog entry is accepted):

```bash
hermes mcp add phagescope --url https://phageagent.deepomics.org/mcp \
  --header "Authorization: Bearer $PHAGESCOPE_MCP_TOKEN"
```

## Checks

- from the repo root: `claude plugin validate .` and `claude plugin validate ./integrations/phagescope-mcp/claude/phagescope`
- `codex plugin marketplace add "$PWD" && codex plugin add phagescope@phagescope && codex mcp list`
- `HERMES_OPTIONAL_MCPS=<dir containing phagescope/manifest.yaml> hermes mcp catalog`
- `registry/server.json` against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
