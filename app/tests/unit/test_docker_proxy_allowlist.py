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
        ("DELETE", "/containers/json"),
        ("GET", "/containers/create"),
    ],
)
def test_refused_surface(method: str, path: str) -> None:
    assert docker_proxy.is_allowed(method, path) is False


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
    for path in ("/", "/anything", "/v1.41/containers/json", "/containers"):
        assert docker_proxy.is_allowed("GET", path) is False
