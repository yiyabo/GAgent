from contextlib import contextmanager
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import artifact_routes
from app.routers.chat.artifact_gallery import build_artifact_gallery_item, filter_gallery_new_images_only
from app.services.artifact_image_versions import revision, version_inline_images


def test_same_name_overwrite_changes_inline_and_gallery_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path))
    root = tmp_path / "session_demo"
    root.mkdir()
    image = root / "chart.png"
    image.write_bytes(b"old chart")
    text = "English chart: ![chart](chart.png)"
    first = version_inline_images(text, "demo")
    first_item = build_artifact_gallery_item("chart.png", session_id="demo")
    image.write_bytes(b"English chart")
    second = version_inline_images(text, "demo")
    second_item = build_artifact_gallery_item("chart.png", session_id="demo")
    assert first != second
    assert second.endswith("?v=" + hashlib.sha256(b"English chart").hexdigest() + ")")
    assert version_inline_images(second, "demo") == second
    assert first_item["content_hash"] != second_item["content_hash"]

    class DB:
        def execute(self, *args):
            return self

        def fetchall(self):
            return [(json.dumps({"artifact_gallery": [first_item]}),)]

    @contextmanager
    def db():
        yield DB()

    monkeypatch.setattr("app.database_pool.get_db", db)
    assert filter_gallery_new_images_only([first_item], session_id="demo") == []
    assert filter_gallery_new_images_only([second_item], session_id="demo") == [second_item]


def test_versions_only_existing_session_images_preserving_other_references(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path))
    root = tmp_path / "session_demo"
    root.mkdir()
    (root / "chart name.png").write_bytes(b"new")
    (tmp_path / "outside.png").write_bytes(b"private")
    (root / "linked.png").symlink_to(tmp_path / "outside.png")
    text = "\n".join(
        [
            "![x](chart%20name.png?v=old#panel)",
            "![x](/api/artifacts/sessions/demo/file?path=chart+name.png)",
            "![x](https://external.example/chart.png)",
            "![x](/api/artifacts/sessions/other/file?path=chart+name.png)",
            "![x](missing.png)",
            "![x](../outside.png)",
            "![x](linked.png)",
            "```md",
            "![x](chart%20name.png)",
            "```",
        ]
    )
    result = version_inline_images(text, "demo").splitlines()
    token = hashlib.sha256(b"new").hexdigest()
    assert result[0] == f"![x](chart%20name.png?v={token}#panel)"
    query = parse_qs(urlsplit(result[1][5:-1]).query)
    assert query == {"path": ["chart name.png"], "v": [token]}
    assert result[2:] == text.splitlines()[2:]
    assert revision("linked.png", "demo") is None


def test_mutable_image_endpoint_revalidates_and_serves_replaced_bytes(tmp_path, monkeypatch):
    root = tmp_path / "session_demo"
    root.mkdir()
    image = root / "chart.png"
    image.write_bytes(b"Chinese chart")
    monkeypatch.setattr(artifact_routes, "_ensure_session_access", lambda *a: None)
    monkeypatch.setattr(artifact_routes, "_resolve_session_dir", lambda *a, **k: root)
    monkeypatch.setattr(artifact_routes, "_load_hidden_artifact_prefixes", lambda *a: [])
    app = FastAPI()
    app.include_router(artifact_routes.router)
    with TestClient(app) as client:
        first = client.get("/artifacts/sessions/demo/file?path=chart.png&v=old")
        assert first.status_code == 200 and first.content == b"Chinese chart"
        image.write_bytes(b"English chart")
        second = client.get("/artifacts/sessions/demo/file?path=chart.png&v=new")
        assert second.status_code == 200 and second.content == b"English chart"
        assert second.headers["cache-control"] == "private, no-cache"
        assert client.get("/artifacts/sessions/demo/file?path=../outside.png").status_code == 400
