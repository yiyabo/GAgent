"""Deploy-time network policy for the Docker API proxy (deploy/docker-compose.yml).

The proxy holds the daemon socket, so the network that carries it must never be
one that model-written code can join. Sandboxes are created on demand with
``--network gagent-sandbox`` (app/ops/sandbox.py), so the proxy lives on its own
``gagent-dockerapi`` network shared with the app only. Without this, code inside
a sandbox can POST to the proxy and create containers with arbitrary host binds
(LOCAL_INFRA §164). These tests fail if a future edit puts the two back
together, and pin the app-side contract the change relies on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Set

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"

SANDBOX_NETWORK = "sandbox"
DOCKER_API_NETWORK = "dockerapi"


def _networks(service: dict[str, Any]) -> Set[str]:
    """Service networks as a set, tolerating the list and mapping YAML forms."""
    nets = service.get("networks") or []
    return set(nets) if not isinstance(nets, dict) else set(nets)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    assert COMPOSE_PATH.is_file(), f"compose file missing: {COMPOSE_PATH}"
    return yaml.safe_load(COMPOSE_PATH.read_text())


def test_proxy_is_not_on_the_sandbox_network(compose: dict[str, Any]) -> None:
    proxy = _networks(compose["services"]["proxy"])
    assert proxy == {DOCKER_API_NETWORK}, (
        "the Docker API proxy must not share a network with anything that runs "
        "model-written code (sandboxes join gagent-sandbox)"
    )


def test_only_the_app_shares_the_sandbox_network(compose: dict[str, Any]) -> None:
    on_sandbox = {
        name
        for name, service in compose["services"].items()
        if SANDBOX_NETWORK in _networks(service)
    }
    assert on_sandbox == {"app"}, f"unexpected members of gagent-sandbox: {on_sandbox}"


def test_app_reaches_proxy_by_service_name(compose: dict[str, Any]) -> None:
    app = compose["services"]["app"]
    assert app["environment"]["DOCKER_HOST"] == "tcp://proxy:2375"
    shared = _networks(app) & _networks(compose["services"]["proxy"])
    assert shared == {DOCKER_API_NETWORK}, (
        "the app and the proxy must share exactly the dedicated Docker API "
        f"network so 'proxy' resolves; got {shared or '{}'}"
    )


def test_dedicated_and_sandbox_networks_stay_internal(compose: dict[str, Any]) -> None:
    networks = compose["networks"]
    for name in (SANDBOX_NETWORK, DOCKER_API_NETWORK):
        assert networks[name]["internal"] is True, f"{name} must stay internal"
    subnets = [entry["ipam"]["config"][0]["subnet"] for entry in networks.values()]
    assert len(subnets) == len(set(subnets)), f"subnet collision: {subnets}"
