# PhageScope MCP (`/mcp`)

Read-only MCP server over the PhageScope data service, served by the GAgent app
at `https://phageagent.deepomics.org/mcp` (Streamable HTTP, stateless, JSON
responses). Design: `design/2026-11-19-phagescope-mcp.md` (phase 1).

## Tools

`catalog_overview`, `search_phages`, `cohort_report`, `aggregate_phages`,
`get_phage`, `get_sequence`, `search_proteins`, `search_families`,
`family_members`, `query_features`, `get_structures`, plus the resource
`phagescope://reference/query-guide`. Every tool is annotated read-only.

Each result is one JSON text block: `data` (service payload), `provenance`
(source, snapshot, retrieval time), `limits` (`returned_rows`, `truncated`,
paging) and `notes`. A null field means unknown, never zero.

## Access and limits

| Setting | Default | Meaning |
|---|---|---|
| `PHAGESCOPE_MCP_ENABLED` | `0` | `/mcp` answers 404 until set to `1` |
| `PHAGESCOPE_MCP_ALLOWED_USERS` | empty | user ids / emails that may self-issue tokens (admins always may) |
| `PHAGESCOPE_MCP_DAILY_BYTES` | 2 GiB | returned bytes per token per UTC day |
| `PHAGESCOPE_MCP_REQUESTS_PER_MINUTE` | 60 | per token, per app process |
| `PHAGESCOPE_MCP_MAX_CONCURRENCY` | 4 | in-flight requests per token, per app process |
| `PHAGESCOPE_MCP_MAX_RESULT_BYTES` | 512 KiB | per call; larger results are trimmed by rows and flagged `truncated` |

Tokens look like `gpsk_...`; only their SHA-256 is stored. Every call is
audited (token id, tool, argument digest and short summary, status, duration,
rows, bytes); plaintext tokens are never logged.

Issue tokens:

- Allowlisted user, signed in: `POST /phagescope-mcp/tokens {"name": "codex"}`
  (list: `GET`, revoke: `DELETE /phagescope-mcp/tokens/{id}`).
- Operator: `python scripts/phagescope_mcp_tokens.py issue --user-id <id> --name codex`
  (`list`, `revoke <id>`, `audit <id>`).

## Client setup

Replace `$PHAGESCOPE_MCP_TOKEN` with the token.

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.phagescope]
url = "https://phageagent.deepomics.org/mcp"
bearer_token_env_var = "PHAGESCOPE_MCP_TOKEN"
```

Claude Code:

```bash
claude mcp add --transport http phagescope https://phageagent.deepomics.org/mcp \
  --header "Authorization: Bearer $PHAGESCOPE_MCP_TOKEN"
```

Clients with a JSON `mcpServers` file and HTTP support (WorkBuddy, Cursor, Hermes, Devin):

```json
{"mcpServers": {"phagescope": {
  "url": "https://phageagent.deepomics.org/mcp",
  "headers": {"Authorization": "Bearer ${PHAGESCOPE_MCP_TOKEN}"}}}}
```

stdio-only clients can bridge with `mcp-remote`:

```json
{"mcpServers": {"phagescope": {"command": "npx", "args": [
  "-y", "mcp-remote", "https://phageagent.deepomics.org/mcp",
  "--header", "Authorization: Bearer ${PHAGESCOPE_MCP_TOKEN}"]}}}
```

## Verify

`python scripts/verify_phagescope_mcp.py --url <base>/mcp --token <token>` runs
initialize, tools/list, resources/read and a few read-only tool calls with the
official MCP client.
