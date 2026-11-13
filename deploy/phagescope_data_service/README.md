# GAgent PhageScope V2 Read-Only Data Service

Self-contained read-only service owned by GAgent (`/data3/platform/gagent/phagescope-service/`).
Requires **zero modifications** to PhageScope's website codebase or PostgreSQL tables.

## Safety Guarantees

1. Connects strictly as `phage_ro` (`NOSUPERUSER`, `default_transaction_read_only=on`, `statement_timeout=10000ms`, max 4 pooled connections).
2. Startup self-check verifies `INSERT`, `UPDATE`, and `DELETE` privileges on `phages` are all `False`; exits immediately otherwise.
3. Binds `127.0.0.1:18326` on the host and requires constant-time `X-API-Key` validation. `gagent-forwarder-1` exposes `18326` only on the internal Docker bridge (`FORWARD_BIND`).
4. Serves both legacy `/agent-api/v1/data/*` GET endpoints (`phagescope(action="gateway")`) and research-grade `/agent-api/v1/data/v2/*` POST endpoints (`phagescope_data`).
