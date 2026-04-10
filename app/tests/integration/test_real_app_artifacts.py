from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _ensure_session(client, session_id: str) -> None:
    response = client.patch(
        f"/chat/sessions/{session_id}",
        json={"name": f"Session {session_id}"},
    )
    assert response.status_code == 200


@pytest.mark.integration
def test_real_app_artifact_endpoints_use_isolated_runtime_roots(
    app_client_factory,
    isolated_app_env,
) -> None:
    session_id = "artifactprod001"
    session_dir = isolated_app_env["runtime_root"] / f"session_{session_id}"
    latest_root = session_dir / "deliverables" / "latest"

    raw_file = session_dir / "tool_outputs" / "analysis" / "notes.txt"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("raw artifact notes\n", encoding="utf-8")

    report_file = latest_root / "paper" / "final" / "report.md"
    summary_file = latest_root / "docs" / "release_summary.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("# Final report\nproduction ready\n", encoding="utf-8")
    summary_file.write_text("ready for review\n", encoding="utf-8")

    _write_json(
        session_dir / "deliverables" / "manifest_latest.json",
        {
            "version_id": "v20260321",
            "items": [
                {
                    "module": "paper",
                    "path": "paper/final/report.md",
                    "status": "final",
                },
                {
                    "module": "docs",
                    "path": "docs/release_summary.md",
                    "status": "final",
                },
            ],
            "paper_status": {
                "completed_sections": ["results"],
                "missing_sections": [],
                "total_sections": 1,
                "completed_count": 1,
            },
        },
    )

    with app_client_factory() as client:
        _ensure_session(client, session_id)
        listing_response = client.get(f"/artifacts/sessions/{session_id}")
        assert listing_response.status_code == 200
        listing_paths = {
            item["path"] for item in listing_response.json()["items"]
        }
        assert "tool_outputs/analysis/notes.txt" in listing_paths

        raw_text_response = client.get(
            f"/artifacts/sessions/{session_id}/text",
            params={"path": "tool_outputs/analysis/notes.txt"},
        )
        assert raw_text_response.status_code == 200
        assert raw_text_response.json()["content"] == "raw artifact notes\n"

        deliverable_list = client.get(
            f"/artifacts/sessions/{session_id}/deliverables"
        )
        assert deliverable_list.status_code == 200
        deliverable_payload = deliverable_list.json()
        assert deliverable_payload["count"] == 2
        assert set(deliverable_payload["modules"]) == {"docs", "paper"}
        assert deliverable_payload["paper_status"]["completed_count"] == 1

        deliverable_text = client.get(
            f"/artifacts/sessions/{session_id}/deliverables/text",
            params={"path": "paper/final/report.md"},
        )
        assert deliverable_text.status_code == 200
        assert "production ready" in deliverable_text.json()["content"]

        deliverable_file = client.get(
            f"/artifacts/sessions/{session_id}/deliverables/file",
            params={"path": "docs/release_summary.md"},
        )
        assert deliverable_file.status_code == 200
        assert "ready for review" in deliverable_file.text


@pytest.mark.integration
def test_real_app_artifact_endpoints_hide_blocked_release_paths_and_reject_traversal(
    app_client_factory,
    isolated_app_env,
) -> None:
    session_id = "artifactblocked001"
    session_dir = isolated_app_env["runtime_root"] / f"session_{session_id}"
    latest_root = session_dir / "deliverables" / "latest"

    hidden_raw = (
        session_dir
        / "tool_outputs"
        / "review_pack_writer"
        / "review_pack_20260311_000000"
        / "review_draft.md"
    )
    hidden_paper = latest_root / "paper" / "sections" / "abstract.tex"
    visible_summary = latest_root / "docs" / "release_summary.md"

    hidden_raw.parent.mkdir(parents=True, exist_ok=True)
    hidden_paper.parent.mkdir(parents=True, exist_ok=True)
    visible_summary.parent.mkdir(parents=True, exist_ok=True)

    hidden_raw.write_text("hidden draft\n", encoding="utf-8")
    hidden_paper.write_text("\\begin{abstract}hidden\\end{abstract}\n", encoding="utf-8")
    visible_summary.write_text("Publication blocked.\n", encoding="utf-8")

    _write_json(
        session_dir / "deliverables" / "manifest_latest.json",
        {
            "version_id": "v-blocked",
            "release_state": "blocked",
            "public_release_ready": False,
            "release_summary": "Publication blocked.",
            "hidden_artifact_prefixes": [
                "paper",
                "tool_outputs/review_pack_writer/review_pack_20260311_000000",
            ],
            "items": [
                {
                    "module": "docs",
                    "path": "docs/release_summary.md",
                    "status": "final",
                }
            ],
            "paper_status": {
                "completed_sections": ["abstract"],
                "missing_sections": ["introduction"],
                "total_sections": 2,
                "completed_count": 1,
            },
        },
    )

    with app_client_factory() as client:
        _ensure_session(client, session_id)
        deliverable_response = client.get(
            f"/artifacts/sessions/{session_id}/deliverables",
            params={"include_draft": "true"},
        )
        assert deliverable_response.status_code == 200
        deliverable_payload = deliverable_response.json()
        assert deliverable_payload["release_state"] == "blocked"
        assert deliverable_payload["public_release_ready"] is False
        assert deliverable_payload["count"] == 1
        assert deliverable_payload["items"][0]["path"] == "docs/release_summary.md"
        assert "paper" not in deliverable_payload["modules"]

        raw_listing = client.get(f"/artifacts/sessions/{session_id}")
        assert raw_listing.status_code == 200
        assert raw_listing.json()["count"] == 0

        hidden_response = client.get(
            f"/artifacts/sessions/{session_id}/text",
            params={
                "path": "tool_outputs/review_pack_writer/review_pack_20260311_000000/review_draft.md"
            },
        )
        assert hidden_response.status_code == 404

        traversal_response = client.get(
            f"/artifacts/sessions/{session_id}/text",
            params={"path": "../outside.txt"},
        )
        assert traversal_response.status_code == 400
        assert traversal_response.json()["success"] is False
        assert traversal_response.json()["error"]["message"] == "Invalid artifact path"


@pytest.mark.integration
def test_real_app_missing_deliverables_for_existing_session_returns_empty_payload(
    app_client_factory,
) -> None:
    with app_client_factory() as client:
        _ensure_session(client, "missing-production-session")
        response = client.get(
            "/artifacts/sessions/missing-production-session/deliverables"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 0
        assert payload["items"] == []
        assert payload["modules"] == {}
        assert payload["root_path"] == ""
