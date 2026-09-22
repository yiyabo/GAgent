"""Consul registration lifecycle: default-off and fail-open guarantees."""

from __future__ import annotations

import pytest

from app.consul_registration import ConsulRegistration


@pytest.fixture()
def _clean_consul_env(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "CONSUL_ENABLED",
        "CONSUL_URL",
        "AGENT_SERVICE_NAME",
        "AGENT_INSTANCE_ID",
        "AGENT_SERVICE_ADDRESS",
        "AGENT_SERVICE_PORT",
        "AGENT_HEALTH_CHECK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_consul_registration_disabled_by_default(_clean_consul_env) -> None:
    reg = ConsulRegistration()
    assert reg.enabled is False
    reg.start()
    assert reg._thread is None
    reg.stop()


def test_consul_registration_failure_is_fail_open(_clean_consul_env) -> None:
    _clean_consul_env.setenv("CONSUL_ENABLED", "1")
    # 127.0.0.1:1 refuses fast; the retry loop must never block or raise.
    _clean_consul_env.setenv("CONSUL_URL", "http://127.0.0.1:1")
    reg = ConsulRegistration()
    assert reg.enabled is True
    reg.start()
    assert reg._thread is not None
    reg.stop()
    assert reg._registered is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_consul_enabled_truthy_variants(_clean_consul_env, raw: str) -> None:
    _clean_consul_env.setenv("CONSUL_ENABLED", raw)
    assert ConsulRegistration().enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "junk"])
def test_consul_enabled_falsy_variants(_clean_consul_env, raw: str) -> None:
    _clean_consul_env.setenv("CONSUL_ENABLED", raw)
    assert ConsulRegistration().enabled is False
