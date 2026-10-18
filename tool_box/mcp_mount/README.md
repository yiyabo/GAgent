# Mounting external MCP servers

Attach any MCP server (a colleague's component, a public one) to the agent by
editing a JSON file. No code change, no restart-free magic: the file is read at
startup and again when `reload` is called.

Off by default. Without `MCP_MOUNTS_FILE` nothing is mounted and behaviour is
unchanged.

```bash
export MCP_MOUNTS_FILE=/path/to/mcp_mounts.json
```

```json
{
  "servers": {
    "phage_lab": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "my_component.server"],
      "cwd": "/srv/components/phage_lab",
      "env": {"MY_COMPONENT_DATA": "/srv/data"},
      "read_only_tools": ["search_genomes"],
      "timeout_sec": 120,
      "max_result_chars": 20000
    },
    "remote_docs": {
      "transport": "http",
      "url": "https://example.org/mcp",
      "bearer_token_env": "REMOTE_DOCS_TOKEN"
    }
  }
}
```

| field | meaning |
|---|---|
| `transport` | `stdio` (we start the process) or `http` (Streamable HTTP) |
| `command`, `args`, `cwd`, `env` | stdio only. The child gets only `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TERM` plus what you list in `env`. **Our own secrets are not inherited.** |
| `url`, `bearer_token_env` | http only. `bearer_token_env` is the *name* of an environment variable holding the token. Never put the token in this file. |
| `read_only_tools` | Tools you vouch for as read-only. Everything else is treated as having side effects. There is no wildcard. |
| `timeout_sec` | Per call, 1-600 (default 60) |
| `max_result_chars` | Output cap per call, 200-200000 (default 20000) |
| `enabled` | `false` keeps the entry but does not mount it |

## What the agent sees

Each tool becomes `mcp__<server>__<tool>`, e.g. `mcp__phage_lab__search_genomes`.
The prefix means a mounted tool can never replace a built-in one.

## Trust rules (applied to every mounted tool)

- **Untrusted output.** Results come back as data with `untrusted: true` and a
  notice telling the model not to follow instructions inside them. The text is
  truncated to `max_result_chars`.
- **Side effects assumed.** Not marked read-only, not run in parallel with other
  tools, unless listed in `read_only_tools`.
- **No reverse requests.** A mounted server cannot ask us to run the model
  (sampling), prompt the user, or list our directories. Those requests are refused.
- **Failures are contained.** A server that is down, slow or crashes yields an
  error result for that call. It never blocks application startup or other mounts.
- **Only tools** are mounted. MCP resources and prompts are ignored.
- The plan executor's fixed tool list does not include mounted tools; they are
  offered to the chat agent only.

## Plugging in and out while running

`await get_mount_manager().load(path)` re-reads the file: new entries are
connected, removed or changed entries are disconnected and reconnected, tools
come and go. A file that cannot be read keeps the current mounts. There is no
HTTP endpoint for this yet.

## Quick check

```bash
python -m tool_box.mcp_server   # our own read-only server, usable as a test target
```

mount it as a `stdio` server and the agent can call `mcp__gagent__load_skill`.
