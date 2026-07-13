from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from tool_box.watermark import WATERMARK_TEXT, apply_watermark


def test_watermark_marks_markdown_idempotently(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    source.write_text("# Report\n", encoding="utf-8")

    apply_watermark(source, first)
    apply_watermark(first, second)

    assert first.read_text(encoding="utf-8").count(WATERMARK_TEXT) == 1
    assert second.read_text(encoding="utf-8") == first.read_text(encoding="utf-8")


def test_watermark_marks_png_idempotently(tmp_path: Path) -> None:
    source = tmp_path / "figure.png"
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (120, 80), color="white").save(source)

    apply_watermark(source, first)
    apply_watermark(first, second)

    with Image.open(second) as rendered:
        assert rendered.info["_watermark"] == WATERMARK_TEXT
    assert second.read_bytes() == first.read_bytes()


def test_watermark_preserves_machine_readable_artifacts(tmp_path: Path) -> None:
    fixtures = {
        "records.csv": "name,value\na,1\n",
        "records.tsv": "name\tvalue\na\t1\n",
        "sequence.fasta": ">seq\nATGC\n",
        "records.json": json.dumps({"name": "sample", "value": 1}),
        "records.jsonl": json.dumps({"name": "sample"}) + "\n",
        "table.xlsx": "not-an-xlsx",
        "references.bib": "@article{sample, title={Example}}\n",
    }

    for filename, content in fixtures.items():
        source = tmp_path / filename
        target = tmp_path / f"copy-{filename}"
        source.write_text(content, encoding="utf-8")

        apply_watermark(source, target)

        assert target.read_bytes() == source.read_bytes(), filename
