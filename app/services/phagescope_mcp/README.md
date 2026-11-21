# PhageScope MCP (`/mcp`)

Read-only MCP server over the PhageScope data service, served by the GAgent app
at `https://phageagent.deepomics.org/mcp` (Streamable HTTP, stateless, JSON
responses). Design: `design/2026-11-19-phagescope-mcp.md` (phase 1).

## Tools

Every token with `data:read` gets the 13 data tools (annotated read-only);
`analysis:submit` adds the 6 analysis-task tools (`analysis.py`), 19 tools in
all. Plus the resource `phageagent://reference/query-guide`.

<!-- tools:begin -->
- `catalog_overview`
- `search_phages`
- `cohort_report`
- `aggregate_phages`
- `get_phage`
- `get_sequence`
- `search_proteins`
- `search_families`
- `family_members`
- `query_features`
- `get_structures`
- `similarity_search`
- `get_similarity_result`
- `submit_analysis`
- `get_analysis_status`
- `get_analysis_result`
- `reply_analysis`
- `cancel_analysis`
- `list_analyses`
<!-- tools:end -->

Adding or removing a tool means updating this list and the Tools table in
`integrations/phageagent-mcp/README.md`; `app/tests/tools/test_phagescope_mcp_docs.py`
compares both with the server's `tools/list`.

Each result is one JSON text block: `data` (service payload), `provenance`
(source, snapshot, retrieval time), `limits` (`returned_rows`, `truncated`,
paging) and `notes`. A null field means unknown, never zero.

## Access and limits

| Setting | Default | Meaning |
|---|---|---|
| `PHAGESCOPE_MCP_ENABLED` | `0` | `/mcp` answers 404 until set to `1` |
| `PHAGESCOPE_MCP_ALLOWED_USERS` | empty | user ids / emails that may self-issue tokens (admins always may) |
| `PHAGESCOPE_MCP_ALLOWED_USER_IDS` | empty | immutable user ids that may self-issue/use tokens; required alongside email entries when open signup is enabled |
| `PHAGESCOPE_MCP_OPEN_ACCESS` | `0` | `1` lets every signed-in, active user self-issue tokens without an allowlist entry; they get `data:read`, plus `analysis:submit` when analysis is enabled |
| `PHAGESCOPE_MCP_ANON_DISCOVERY` | `0` | `1` lets requests without a token `initialize`, `ping` and `tools/list` (no tool calls, no resources), so directories such as mcptry can list the tools |
| `PHAGESCOPE_MCP_ANON_REQUESTS_PER_MINUTE` | `30` | rate limit for those token-less requests, per client IP (`X-Real-IP` from the gateway) |
| `PHAGESCOPE_MCP_ALLOWED_HOSTS` | `phageagent.deepomics.org,localhost,127.0.0.1` | exact Host values accepted by the `/mcp` transport |
| `PHAGESCOPE_MCP_ALLOWED_ORIGINS` | `https://phageagent.deepomics.org,http://localhost,http://127.0.0.1` | exact Origin values accepted by the `/mcp` transport |
| `PHAGESCOPE_MCP_DAILY_BYTES` | 2 GiB | returned bytes per token per UTC day |
| `PHAGESCOPE_MCP_REQUESTS_PER_MINUTE` | 60 | per token, per app process |
| `PHAGESCOPE_MCP_MAX_CONCURRENCY` | 4 | in-flight requests per token, per app process |
| `PHAGESCOPE_MCP_MAX_RESULT_BYTES` | 512 KiB | per call; larger results are trimmed by rows and flagged `truncated` |
| `PHAGESCOPE_MCP_ANALYSIS_DAILY_SUBMISSIONS` | 10 | daily per-user cap for initial submissions plus reply runs; `0` = unlimited (also for the two concurrency caps) |

When `AUTH_OPEN_SIGNUP=1`, an email entry in `PHAGESCOPE_MCP_ALLOWED_USERS` is
not sufficient to issue or use a token: the user's immutable id must also be in
`PHAGESCOPE_MCP_ALLOWED_USER_IDS`. With signup disabled, existing email
allowlisting remains supported.

Token authentication rechecks the owner's active account and the current
allowlist on every request, including tokens issued by the operator CLI.
Configuration changes take effect after the app reloads its settings/restarts.
For an existing user, obtain the immutable `user.user_id` from their signed-in
`GET /auth/me` response before enabling open signup or releasing this policy.
Existing tokens keep working once that id is allowlisted; reissuing is not
required. Admin eligibility is unchanged, but inactive/deleted admins are denied.

The transport rejects unlisted Host/Origin values even with a valid token.
Origins can be absent for non-browser clients. For a local port or an alternate
public hostname, explicitly include its Host and Origin in the comma-separated
settings above. The gateway forwards `Host $host` to the app (not `app:9000`);
the outer Apache proxy must preserve the public Host (`ProxyPreserveHost On`).
The default public Host is `phageagent.deepomics.org`, and loopback Host values
are accepted for local checks. Add any other ingress hostname before deployment.

`PHAGESCOPE_MCP_ANALYSIS_DAILY_SUBMISSIONS` counts initial submissions and all
new reply runs together per user, across their tokens, resetting at 00:00 UTC.
Idempotent retries do not consume another slot; completed, failed and cancelled
runs still count. The default cap remains 10.

Tokens look like `gpsk_...`; only their SHA-256 is stored. Every call is
audited (token id, tool, argument digest and short summary, status, duration,
rows, bytes); plaintext tokens are never logged.

Issue tokens:

- Signed-in user (any user under open access): Extensions -> External access ->
  Create token in the web UI, or `POST /phagescope-mcp/tokens {"name": "codex"}`
  (list: `GET`, revoke: `DELETE /phagescope-mcp/tokens/{id}`).
- Operator: `python scripts/phagescope_mcp_tokens.py issue --user-id <id> --name codex`
  (`list`, `revoke <id>`, `audit <id>`).

## Client setup

Replace `$PHAGEAGENT_MCP_TOKEN` with the token.

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.phageagent]
url = "https://phageagent.deepomics.org/mcp"
bearer_token_env_var = "PHAGEAGENT_MCP_TOKEN"
```

Claude Code:

```bash
claude mcp add --transport http phageagent https://phageagent.deepomics.org/mcp \
  --header "Authorization: Bearer $PHAGEAGENT_MCP_TOKEN"
```

Clients with a JSON `mcpServers` file and HTTP support (WorkBuddy, Cursor, Hermes, Devin):

```json
{"mcpServers": {"phageagent": {
  "url": "https://phageagent.deepomics.org/mcp",
  "headers": {"Authorization": "Bearer ${PHAGEAGENT_MCP_TOKEN}"}}}}
```

stdio-only clients can bridge with `mcp-remote`:

```json
{"mcpServers": {"phageagent": {"command": "npx", "args": [
  "-y", "mcp-remote", "https://phageagent.deepomics.org/mcp",
  "--header", "Authorization: Bearer ${PHAGEAGENT_MCP_TOKEN}"]}}}
```

## Verify

After every release that changes tools, and before publishing the public repo
or the Registry entry, compare the public docs with the live `tools/list`
(anonymous discovery is on, so no token is needed):

```bash
python integrations/phageagent-mcp/scripts/check_tools_doc.py \
  --url https://phageagent.deepomics.org/mcp \
  integrations/phageagent-mcp/README.md \
  https://raw.githubusercontent.com/yiyabo/phageagent-mcp/main/README.md
```

The public repo runs the same check daily and on every push.

`python scripts/verify_phagescope_mcp.py --url <base>/mcp --token <token>` runs
initialize, tools/list, resources/read and a few read-only tool calls with the
official MCP client.
