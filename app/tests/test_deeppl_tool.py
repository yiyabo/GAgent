from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any, Dict

import pytest

deeppl_module = importlib.import_module("tool_box.tools_impl.deeppl")
from app.services import tool_schemas


def test_deeppl_predict_sequence_text_normalizes_and_parses_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "deeppl_out"
    output_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(deeppl_module, "_resolve_output_root", lambda _sid: output_root)

    captured: Dict[str, Any] = {}

    async def _fake_local_predict(
        *,
        normalized_input_path: Path,
        model_path: str | None,
        predict_script: str | None,
        python_bin: str | None,
        timeout_sec: int,
    ) -> Dict[str, Any]:
        captured["normalized_input_path"] = normalized_input_path
        captured["timeout_sec"] = timeout_sec
        _ = (model_path, predict_script, python_bin)
        return {
            "success": True,
            "mode": "local",
            "command": "python predict_lyso_vs_lytic.py ...",
            "stdout": "DeepPL threshold1: 0.9 threshold2: 0.016\n0.2000 Predict: Lysogenic\n",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_seconds": 0.2,
            "model_path": "/tmp/model",
            "predict_script": "/tmp/predict.py",
            "python_bin": "python",
        }

    monkeypatch.setattr(deeppl_module, "_run_local_prediction_command", _fake_local_predict)

    result = asyncio.run(
        deeppl_module.deeppl_handler(
            action="predict",
            sequence_text=("ACGTN" * 30),
            sample_id="sample_a",
            execution_mode="local",
            timeout=120,
        )
    )

    assert result["success"] is True
    assert result["predicted_label"] == "lysogenic"
    assert result["predicted_lifestyle"] == "temperate"
    assert result["sequence_length_raw"] == 150
    assert result["sequence_length_actg"] == 120
    assert result["removed_n_count"] == 30
    prepared = Path(captured["normalized_input_path"])
    assert prepared.exists()
    contents = prepared.read_text(encoding="utf-8")
    assert contents.startswith(">sample_a\n")
    assert "N" not in contents


def test_deeppl_predict_rejects_ambiguous_input() -> None:
    result = asyncio.run(
        deeppl_module.deeppl_handler(
            action="predict",
            input_file="/tmp/a.fasta",
            sequence_text="ACGTACGT",
            execution_mode="local",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "sequence_input_ambiguous"
    assert result["no_claude_fallback"] is True


def test_deeppl_predict_returns_parse_error_when_output_is_unexpected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "deeppl_out"
    output_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(deeppl_module, "_resolve_output_root", lambda _sid: output_root)

    async def _fake_local_predict(
        *,
        normalized_input_path: Path,
        model_path: str | None,
        predict_script: str | None,
        python_bin: str | None,
        timeout_sec: int,
    ) -> Dict[str, Any]:
        _ = (normalized_input_path, model_path, predict_script, python_bin, timeout_sec)
        return {
            "success": True,
            "mode": "local",
            "command": "python predict_lyso_vs_lytic.py ...",
            "stdout": "DeepPL threshold1: 0.9 threshold2: 0.016\n(no prediction line)\n",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_seconds": 0.2,
            "model_path": "/tmp/model",
            "predict_script": "/tmp/predict.py",
            "python_bin": "python",
        }

    monkeypatch.setattr(deeppl_module, "_run_local_prediction_command", _fake_local_predict)

    result = asyncio.run(
        deeppl_module.deeppl_handler(
            action="predict",
            sequence_text=("ACGT" * 40),
            execution_mode="local",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "prediction_parse_failed"
    assert result["error_stage"] == "output_parsing"


def test_deeppl_job_status_requires_job_id() -> None:
    result = asyncio.run(deeppl_module.deeppl_handler(action="job_status"))
    assert result["success"] is False
    assert result["error_code"] == "missing_job_id"


def test_tool_schema_contains_deeppl_definition() -> None:
    schema = tool_schemas.TOOL_REGISTRY["deeppl"]
    props = schema["function"]["parameters"]["properties"]
    assert schema["function"]["name"] == "deeppl"
    assert "action" in props
    assert "sequence_text" in props
    assert "remote_profile" in props


def test_build_remote_env_keys_for_gpu_profile() -> None:
    keys = deeppl_module._build_remote_env_keys("gpu", "HOST", include_bio_fallback=True)
    assert keys == [
        "DEEPPL_REMOTE_GPU_HOST",
        "DEEPPL_REMOTE_HOST",
        "BIO_TOOLS_REMOTE_HOST",
    ]


def test_build_remote_env_keys_for_default_profile() -> None:
    keys = deeppl_module._build_remote_env_keys("default", "HOST", include_bio_fallback=True)
    assert keys == [
        "DEEPPL_REMOTE_HOST",
        "BIO_TOOLS_REMOTE_HOST",
    ]
