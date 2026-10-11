"""Operations modules that run as separate compose services in the stack.

Currently:
  * ``docker_proxy`` — the stack's only path to the Docker daemon (service)
  * ``port_forward`` — host-network forwarder for the host's loopback tunnels (service)
  * ``sandbox`` — shared hardening + host-path translation for sibling sandbox
    containers; imported by the app, never run as a service (P3)

P4 adds retention/backup here (``python -m app.ops.retention``).
"""
