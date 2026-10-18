"""Payload-level policy tests for the Docker API proxy (app/ops/docker_policy.py).

The allowed payloads mirror what each real container lane sends (audit
2026-10-18: execute_code kernel, terminal PTY, interpreter, bio_tools, qwen-code
driver), so the policy cannot be tightened into refusing a legitimate container.
The denied cases are the escape shapes the proxy must never forward: host binds,
the docker socket, privileged mode, extra capabilities, host namespaces, port
publication, unlabelled containers and non-allowlisted images.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ops import docker_policy as policy_mod

RUNTIME_ROOT = "/data/phage-agent/runtime"
BIO_DB_ROOT = "/mnt/sdm/zczhao/bio_tools_db"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def policy() -> policy_mod.ProxyPolicy:
    return policy_mod.ProxyPolicy(
        runtime_root=RUNTIME_ROOT,
        ro_roots=("/data", BIO_DB_ROOT),
        allowed_images=frozenset(
            {
                "gagent-sandbox-python:p1",
                "gagent-python-runtime:latest",
                "staphb/seqkit:2.8.0",
            }
        ),
        allowed_image_prefixes=("staphb/", "quay.io/biocontainers/"),
    )


def _decide(payload: dict, policy: policy_mod.ProxyPolicy) -> policy_mod.PolicyDecision:
    return policy_mod.evaluate_container_create(json.dumps(payload).encode(), policy)


# --- legitimate lanes ------------------------------------------------------

def test_execute_code_sandbox_payload_is_allowed(policy):
    payload = {
        "Image": "gagent-sandbox-python:p1",
        "Labels": {"gagent.session": "session_1", "gagent.kind": "python"},
        "HostConfig": {
            "Binds": [
                f"{RUNTIME_ROOT}/session_1:/app/runtime/session_1:ro",
                f"{RUNTIME_ROOT}/session_1/tasks/1:/app/runtime/session_1/tasks/1:rw",
            ],
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "NetworkMode": "gagent-sandbox",
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "rw,size=2g"},
            "PidsLimit": 512,
        },
    }
    assert _decide(payload, policy).allowed


def test_interpreter_sdk_payload_with_host_network_is_allowed(policy):
    payload = {
        "Image": "gagent-python-runtime:latest",
        "Labels": {"gagent.kind": "interpreter"},
        "Volumes": {
            f"{RUNTIME_ROOT}/session_1/task": {"bind": f"{RUNTIME_ROOT}/session_1/task", "mode": "rw"}
        },
        "HostConfig": {"NetworkMode": "host"},
    }
    assert _decide(payload, policy).allowed


def test_bio_tools_payload_with_reference_db_is_allowed(policy):
    payload = {
        "Image": "staphb/seqkit:2.8.0",
        "Labels": {"gagent.kind": "bio_tools"},
        "HostConfig": {
            "Binds": [
                f"{RUNTIME_ROOT}/bio_tools/seqkit:/work",
                f"{BIO_DB_ROOT}/checkm_data:/work/database:ro",
                f"{RUNTIME_ROOT}/session_1/raw_files:/input:ro",
            ],
            "AutoRemove": True,
        },
    }
    assert _decide(payload, policy).allowed


def test_prefix_allowlisted_image_is_allowed(policy):
    payload = {
        "Image": "quay.io/biocontainers/metabat2:2.15--h986a166_1",
        "Labels": {"gagent.kind": "bio_tools"},
    }
    assert _decide(payload, policy).allowed


def test_untagged_runtime_image_defaults_to_latest(policy):
    payload = {"Image": "gagent-python-runtime", "Labels": {"gagent.kind": "interpreter"}}
    assert _decide(payload, policy).allowed


# --- denied shapes ---------------------------------------------------------

@pytest.mark.parametrize(
    "payload,reason_fragment",
    [
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Binds": ["/:/host"]},
            },
            "writable_bind_outside_runtime_root",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Binds": ["/var/run/docker.sock:/var/run/docker.sock"]},
            },
            "denied_bind_path",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Binds": ["/data/docker/volumes/gagent-data/_data:/data:ro"]},
            },
            "denied_bind_path",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Binds": ["/data/home:/home:ro"]},
            },
            "denied_bind_path",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Binds": ["/data:/data"]},
            },
            "writable_bind_outside_runtime_root",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Privileged": True},
            },
            "host_config_privileged",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"CapAdd": ["SYS_ADMIN"]},
            },
            "host_config_cap_add",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"PidMode": "host"},
            },
            "host_config_pid_mode",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"NetworkMode": "container:gagent-app-1"},
            },
            "host_config_network",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"PortBindings": {"2375/tcp": [{"HostPort": "2375"}]}},
            },
            "host_config_port_binding",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"SecurityOpt": ["seccomp=unconfined"]},
            },
            "host_config_security_opt",
        ),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Mounts": [{"Type": "volume", "Source": "somevol", "Target": "/x"}]},
            },
            "named_volume_bind",
        ),
        ({"Image": "alpine:latest", "Labels": {"gagent.kind": "python"}}, "image_not_allowed"),
        ({"Image": "gagent-sandbox-python:p1"}, "missing_gagent."),
        (
            {
                "Image": "gagent-sandbox-python:p1",
                "Labels": {"gagent.kind": "python"},
                "HostConfig": {"Binds": ["somevol:/data"]},
            },
            "named_volume_bind",
        ),
    ],
)
def test_escape_shapes_are_denied(payload, reason_fragment, policy):
    decision = _decide(payload, policy)
    assert not decision.allowed, decision.reason
    assert reason_fragment in decision.reason


def test_invalid_body_is_denied(policy):
    assert not policy_mod.evaluate_container_create(b"{not json", policy).allowed


# --- lifecycle and list scoping -------------------------------------------

def test_lifecycle_requires_gagent_label(policy):
    labelled = {"Config": {"Labels": {"gagent.kind": "python"}}}
    assert policy_mod.evaluate_container_operation("delete", labelled, policy).allowed
    denied = policy_mod.evaluate_container_operation("delete", {"Config": {"Labels": {"com.example": "x"}}}, policy)
    assert not denied.allowed and "unlabelled" in denied.reason


def test_lifecycle_without_inspect_does_not_decide(policy):
    assert policy_mod.evaluate_container_operation("stop", None, policy).allowed


def test_list_requires_gagent_label_filter(policy):
    good = b"filters=" + json.dumps({"label": {"gagent.component=qwen-code-agent": True}}).encode()
    bad = b"filters=" + json.dumps({"status": ["running"]}).encode()
    assert policy_mod.evaluate_containers_list(good, policy).allowed
    assert not policy_mod.evaluate_containers_list(bad, policy).allowed


# --- environment wiring ----------------------------------------------------

def test_load_policy_from_env_is_report_only_without_runtime_root(monkeypatch):
    monkeypatch.delenv("HOST_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("DOCKER_PROXY_POLICY_ENFORCE", "1")
    loaded = policy_mod.load_policy_from_env()
    assert loaded.enforce is False and loaded.configured is False


def test_load_policy_from_env_reads_images_and_roots(monkeypatch):
    monkeypatch.setenv("HOST_RUNTIME_ROOT", RUNTIME_ROOT)
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "gagent-sandbox-python:p1")
    monkeypatch.setenv("DOCKER_PROXY_ALLOWED_RO_ROOTS", BIO_DB_ROOT)
    monkeypatch.setenv("DOCKER_PROXY_POLICY_ENFORCE", "1")
    monkeypatch.setenv(
        "DOCKER_PROXY_BIO_TOOLS_CONFIG", str(REPO_ROOT / "tool_box/bio_tools/tools_config.json")
    )
    loaded = policy_mod.load_policy_from_env()
    assert loaded.enforce is True
    assert loaded.runtime_root == RUNTIME_ROOT
    assert "gagent-sandbox-python:p1" in loaded.allowed_images
    assert "staphb/seqkit:2.8.0" in loaded.allowed_images  # from the real bio_tools config
    assert BIO_DB_ROOT in loaded.ro_roots
