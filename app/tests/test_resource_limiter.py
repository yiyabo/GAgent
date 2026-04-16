from app.services.terminal import resource_limiter as resource_limiter_module
from app.services.terminal.resource_limiter import ResourceLimits


def test_apply_limits_uses_resource_api(monkeypatch) -> None:
    calls = []

    class FakeResource:
        RLIMIT_CPU = 1
        RLIMIT_AS = 2
        RLIMIT_NPROC = 3

        @staticmethod
        def setrlimit(kind, values):
            calls.append((kind, values))

    monkeypatch.setattr(resource_limiter_module, "resource", FakeResource())
    resource_limiter_module.apply_limits_in_child(
        ResourceLimits(cpu_seconds=2, memory_mb=64, max_procs=8)
    )

    assert calls
