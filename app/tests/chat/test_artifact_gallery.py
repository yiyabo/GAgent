from app.routers.chat.artifact_gallery import extract_artifact_gallery_from_result


def test_extract_artifact_gallery_accepts_saved_image_path(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    image_path = runtime_root / "session_demo" / "work" / "phagescope" / "plot.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")

    gallery = extract_artifact_gallery_from_result(
        {"saved_path": str(image_path)},
        session_id="demo",
        source_tool="phagescope",
        tracking_id="track_demo",
        created_at="2026-04-19T00:00:00Z",
    )

    assert gallery == [
        {
            "path": "work/phagescope/plot.png",
            "display_name": "plot.png",
            "source_tool": "phagescope",
            "mime_family": "image",
            "origin": "artifact",
            "created_at": "2026-04-19T00:00:00Z",
            "tracking_id": "track_demo",
        }
    ]
