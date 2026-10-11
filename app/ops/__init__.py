"""Operations modules that run as separate compose services in the stack.

Currently:
  * ``docker_proxy`` — the stack's only path to the Docker daemon (service)
  * ``port_forward`` — host-network forwarder for the host's loopback tunnels (service)
  * ``sandbox`` — shared hardening + host-path translation for sibling sandbox
    containers; imported by the app, never run as a service (P3)
  * ``retention`` — age/size GC for runtime session dirs + DB log tables
    (``python -m app.ops.retention``; dry-run unless ``--apply``)
  * ``backup`` — SQLite online snapshots via ``VACUUM INTO`` + restore
    (``python -m app.ops.backup``; ``--list`` / ``--restore <ts>``)
  * ``cron`` — the ``ops`` compose service entrypoint: sleeps until 03:00
    UTC+8, then backup → retention; Sundays also VACUUM (``python -m app.ops.cron``)
"""
