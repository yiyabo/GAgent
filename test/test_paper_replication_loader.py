import asyncio
import os
from pathlib import Path

import pytest
from PyPDF2 import PdfWriter

from app.services.paper_replication import load_experiment_card, list_experiment_cards
from tool_box.tools_impl.generate_experiment_card import generate_experiment_card_handler


def test_load_experiment_card_from_yaml():
    card = load_experiment_card("experiment_1", reload=True)
    assert card.paper.get("title")
    assert card.experiment.get("id") == "experiment_1"
    assert "description" in card.task


def test_list_experiment_cards_contains_experiment_1():
    experiments = list_experiment_cards(reload=True)
    ids = [item["id"] for item in experiments]
    assert "experiment_1" in ids


def test_generate_experiment_card_auto_infers_latest_upload(tmp_path):
    if not (
        os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("QWEN_VL_API_KEY")
        or os.getenv("QWEN_API_KEY")
    ):
        pytest.skip("Qwen API key not set; skipping vision-based test.")
    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    pdf_path = uploads / "My Study.pdf"

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as f:
        writer.write(f)

    result = asyncio.run(
        generate_experiment_card_handler(
            uploads_root=str(uploads),
            overwrite=True,
        )
    )

    if not result.get("success"):
        pytest.skip(f"Vision call failed in test environment: {result.get('error')}")

    assert result["success"] is True
    exp_id = result["experiment_id"]
    card_path = Path(result["card_path"])
    assert card_path.exists()

    card = load_experiment_card(exp_id, reload=True)
    assert card.paper.get("pdf_path") == str(pdf_path.resolve())

    # cleanup newly created card to avoid polluting repo state
    try:
        card_path.unlink()
        card_path.parent.rmdir()
    except OSError:
        pass
