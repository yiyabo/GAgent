from app.services.terminal.command_filter import CommandFilter, RiskLevel


def test_classify_safe_command() -> None:
    filt = CommandFilter()
    decision = filt.classify("ls -la")
    assert decision.risk_level == RiskLevel.SAFE
    assert decision.requires_approval is False


def test_classify_elevated_command() -> None:
    filt = CommandFilter()
    decision = filt.classify("mkdir -p tmp/demo")
    assert decision.risk_level == RiskLevel.ELEVATED
    assert decision.requires_approval is False


def test_classify_forbidden_command() -> None:
    filt = CommandFilter()
    decision = filt.classify("rm -rf /tmp/demo")
    assert decision.risk_level == RiskLevel.FORBIDDEN
    assert decision.requires_approval is True
