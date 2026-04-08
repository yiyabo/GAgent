from app.config.executor_config import get_executor_settings


def test_executor_settings_default_to_local_backend(monkeypatch) -> None:
    monkeypatch.delenv("CODE_EXECUTION_BACKEND", raising=False)
    get_executor_settings.cache_clear()
    try:
        settings = get_executor_settings()
        assert settings.code_execution_backend == "local"
    finally:
        get_executor_settings.cache_clear()


def test_executor_settings_reads_qwen_shell_timeout(monkeypatch) -> None:
    monkeypatch.setenv("QC_SHELL_TIMEOUT_MS", "420000")
    get_executor_settings.cache_clear()
    try:
        settings = get_executor_settings()
        assert settings.qc_shell_timeout_ms == 420000
    finally:
        get_executor_settings.cache_clear()


def test_executor_settings_default_local_runtime_uses_docker_image(monkeypatch) -> None:
    monkeypatch.delenv("CODE_EXECUTOR_LOCAL_RUNTIME", raising=False)
    monkeypatch.delenv("CODE_EXECUTOR_DOCKER_IMAGE", raising=False)
    get_executor_settings.cache_clear()
    try:
        settings = get_executor_settings()
        assert settings.code_execution_local_runtime == "docker"
        assert settings.code_execution_docker_image == "gagent-python-runtime:latest"
    finally:
        get_executor_settings.cache_clear()


def test_executor_settings_normalize_local_runtime_override(monkeypatch) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_LOCAL_RUNTIME", "local")
    monkeypatch.setenv("CODE_EXECUTOR_DOCKER_IMAGE", "custom:image")
    get_executor_settings.cache_clear()
    try:
        settings = get_executor_settings()
        assert settings.code_execution_local_runtime == "host"
        assert settings.code_execution_docker_image == "custom:image"
    finally:
        get_executor_settings.cache_clear()
