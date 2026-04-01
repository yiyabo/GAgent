from app.config.executor_config import get_executor_settings


def test_executor_settings_default_to_local_backend(monkeypatch) -> None:
    monkeypatch.delenv("CODE_EXECUTION_BACKEND", raising=False)
    get_executor_settings.cache_clear()
    try:
        settings = get_executor_settings()
        assert settings.code_execution_backend == "local"
    finally:
        get_executor_settings.cache_clear()
