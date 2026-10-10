"""Operations modules that run as separate compose services in the stack.

Currently: ``docker_proxy`` — the stack's only path to the Docker daemon.
P4 adds retention/backup here (``python -m app.ops.retention``).
"""
