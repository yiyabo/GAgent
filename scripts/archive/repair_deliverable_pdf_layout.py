#!/usr/bin/env python3
"""将误发布在 deliverables/latest/paper/ 下的参考文献 PDF 移到 refs/，并同步更新 manifest_latest.json。

与 app.services.deliverables.publisher.MANUSCRIPT_PDF_STEMS 保持一致：main/manuscript 等编译产物保留在 paper/。

用法:
  python scripts/repair_deliverable_pdf_layout.py /path/to/session_xxx/deliverables
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

MANUSCRIPT_STEMS = frozenset({"main", "manuscript", "paper", "submission", "preprint"})


def _rebuild_modules(items: list) -> dict[str, list]:
    modules: dict[str, list] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        mod = str(item.get("module") or "").strip().lower() or "docs"
        modules.setdefault(mod, []).append(item)
    for key in modules:
        modules[key].sort(key=lambda r: str(r.get("path") or ""))
    return modules


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: repair_deliverable_pdf_layout.py <path/to/.../deliverables>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    manifest_path = root / "manifest_latest.json"
    latest = root / "latest"
    paper = latest / "paper"
    refs = latest / "refs"
    if not manifest_path.is_file():
        print(f"No manifest at {manifest_path}", file=sys.stderr)
        return 1
    refs.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items")
    if not isinstance(items, list):
        print("Manifest has no items list", file=sys.stderr)
        return 1

    relabeled = 0
    for row in items:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("path") or "").replace("\\", "/").lstrip("/")
        if not raw.lower().endswith(".pdf"):
            continue
        if not raw.startswith("paper/"):
            continue
        stem = Path(raw).stem.lower()
        if stem in MANUSCRIPT_STEMS:
            continue
        name = Path(raw).name
        new_rel = f"refs/{name}"
        old_abs = latest / raw
        new_abs = latest / new_rel
        if old_abs.is_file():
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            if new_abs.exists() and new_abs.resolve() != old_abs.resolve():
                old_abs.unlink(missing_ok=True)
            elif not new_abs.exists():
                shutil.move(str(old_abs), str(new_abs))
        row["path"] = new_rel
        row["module"] = "refs"
        relabeled += 1

    # 处理 manifest 未记录、但仍躺在 paper/ 根目录下的 PDF
    moved_extra = 0
    if paper.is_dir():
        for pdf in sorted(paper.glob("*.pdf")):
            if pdf.stem.lower() in MANUSCRIPT_STEMS:
                continue
            dest = refs / pdf.name
            if dest.exists() and dest.resolve() != pdf.resolve():
                pdf.unlink(missing_ok=True)
                continue
            if not dest.exists():
                shutil.move(str(pdf), str(dest))
                moved_extra += 1

    manifest["modules"] = _rebuild_modules(items)
    manifest["published_modules"] = sorted(manifest["modules"].keys())
    manifest["published_files_count"] = len([i for i in items if isinstance(i, dict) and str(i.get("path") or "").strip()])

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Done: relabeled {relabeled} manifest row(s); "
        f"moved {moved_extra} extra PDF(s) from paper/ to refs/."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
