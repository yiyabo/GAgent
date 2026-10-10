"""Allowlist regressions for the Docker API proxy (app/ops/docker_proxy.py).

This module is a privilege boundary: the app container has no Docker socket and
reaches the daemon only through it. These tests pin the deny-by-default rule and
the exact surface that is forwarded, so a careless widening of the allowlist
fails here first. The proxy replaced the community docker-socket-proxy image,
which this deployment cannot pull (LOCAL_INFRA §121).
"""

from __future__ import annotations

import pytest

from app.ops import docker_proxy


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/_ping"),
        ("HEAD", "/_ping"),
        ("HEAD", "/v1.41/_ping"),
        ("GET", "/version"),
        ("GET", "/info"),
        ("GET", "/containers/json"),
        ("GET", "/containers/abc123/json"),
        ("POST", "/containers/create"),
        ("POST", "/containers/abc123/start"),
        ("POST", "/containers/abc123/stop"),
        ("POST", "/containers/abc123/kill"),
        ("POST", "/containers/abc123/wait"),
        ("DELETE", "/containers/abc123"),
        ("GET", "/images/json"),
        ("GET", "/images/gagent-sandbox-python:latest/json"),
    ],
)
def test_allowed_surface(method: str, path: str) -> None:
    assert docker_proxy.is_allowed(method, path) is True


@pytest.mark.parametrize(
    "method,path",
    [
        # Privilege escalation surfaces that must never be reachable.
        ("POST", "/build"),
        ("POST", "/containers/abc/exec"),
        ("POST", "/exec/abc/start"),
        ("POST", "/containers/abc/attach"),
        ("GET", "/volumes"),
        ("POST", "/volumes/create"),
        ("GET", "/networks"),
        ("POST", "/networks/create"),
        ("GET", "/secrets"),
        ("GET", "/swarm"),
        ("POST", "/swarm/init"),
        ("GET", "/system/df"),
        ("POST", "/auth"),
        ("POST", "/containers/abc/update"),
        ("POST", "/containers/abc/rename"),
        ("GET", "/containers/abc/logs"),
        ("GET", "/containers/abc/top"),
        ("GET", "/containers/abc/export"),
        ("POST", "/images/create"),
        ("POST", "/images/load"),
        ("GET", "/plugins"),
        ("GET", "/configs"),
        ("GET", "/nodes"),
        ("GET", "/services"),
        # Wrong method for an otherwise-allowed path.
        ("POST", "/_ping"),
        ("GET", "/containers/create"),
    ],
)
def test_refused_surface(method: str, path: str) -> None:
    assert docker_proxy.is_allowed(method, path) is False


def test_containers_json_ambiguity_is_documented_not_a_hole() -> None:
    """``/containers/json`` is the LIST endpoint for GET, but ``json`` is also a
    legal container id — so ``DELETE /containers/json`` matches the per-container
    rule. Docker itself disambiguates by method; deleting a container named
    "json" is harmless, so this is recorded rather than special-cased.
    """
    assert docker_proxy.is_allowed("GET", "/containers/json") is True    # list
    assert docker_proxy.is_allowed("DELETE", "/containers/json") is True  # container named "json"


def test_path_prefixes_do_not_leak_through_substring_match() -> None:
    # A prefix match must not let a longer path through.
    assert docker_proxy.is_allowed("GET", "/containers/json/extra") is False
    assert docker_proxy.is_allowed("GET", "/images/json/extra") is False
    assert docker_proxy.is_allowed("GET", "/_ping/extra") is False


def test_container_id_charset_is_restricted() -> None:
    # Path traversal / query smuggling attempts in the id segment are refused.
    assert docker_proxy.is_allowed("GET", "/containers/../../etc/passwd/json") is False
    assert docker_proxy.is_allowed("GET", "/containers/abc/json?size=1") is False


def test_deny_by_default_for_unknown_paths() -> None:
    for path in ("/", "/anything", "/containers"):
        assert docker_proxy.is_allowed("GET", path) is False


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1.41/version"),
        ("GET", "/v1.41/containers/json"),
        ("GET", "/v1.52/containers/abc/json"),
        ("POST", "/v1.41/containers/create"),
        ("DELETE", "/v1.41/containers/abc"),
        ("GET", "/v1.41/images/json"),
    ],
)
def test_versioned_cli_paths_are_allowed(method: str, path: str) -> None:
    """The Docker CLI prefixes every path with its API version."""
    assert docker_proxy.is_allowed(method, path) is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1.41/build"),
        ("POST", "/v1.41/containers/abc/exec"),
        ("GET", "/v1.41/volumes"),
        ("GET", "/v1.41/secrets"),
        ("POST", "/v1.41/swarm/init"),
    ],
)
def test_version_prefix_does_not_widen_the_surface(method: str, path: str) -> None:
    assert docker_proxy.is_allowed(method, path) is False


def test_version_stripping_does_not_accept_junk() -> None:
    # Only a well-formed /v<digits>[.<digits>] prefix is stripped.
    assert docker_proxy.is_allowed("GET", "/vX/version") is False
    assert docker_proxy.is_allowed("GET", "/version/extra") is False
