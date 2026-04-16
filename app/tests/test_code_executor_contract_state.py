from tool_box.tools_impl import code_executor as code_executor_module


def test_clear_stale_contract_failure_state_when_verification_passes() -> None:
    summary, guidance = code_executor_module._clear_stale_contract_failure_state(
        success=True,
        verification_status="passed",
        contract_error_summary="file_nonempty: /tmp/simple_line_chart.png (File is missing or empty.)",
        contract_fix_guidance="write the file to the expected path",
    )

    assert summary is None
    assert guidance is None


def test_clear_stale_contract_failure_state_preserves_real_failure() -> None:
    summary, guidance = code_executor_module._clear_stale_contract_failure_state(
        success=False,
        verification_status="failed",
        contract_error_summary="file_nonempty: /tmp/simple_line_chart.png (File is missing or empty.)",
        contract_fix_guidance="write the file to the expected path",
    )

    assert summary == "file_nonempty: /tmp/simple_line_chart.png (File is missing or empty.)"
    assert guidance == "write the file to the expected path"
