Services Overview.

Purpose:
- Centralize environment and configuration access with `app.services.settings.get_settings()`.
- Keep GLM Embeddings config in one place and reduce scattered `os.getenv` calls.

Highlights:
- Use `get_settings()` for all config (logging, DB, LLM, embeddings, cache sizes).
- `app.services.config.get_config()` builds a typed GLM Embeddings config from settings.
- Embedding caches read sizes and persistence from settings (not environment).

Preferred Imports:
- LLM chat: `from app.llm import get_default_client`
- LLM service: `from app.services.llm_service import get_llm_service`
- Embeddings: `from app.services.embeddings import get_embeddings_service`
- Settings: `from app.services.settings import get_settings`
- Embedding Config: `from app.services.config import get_config`

Env Vars (selected):
- LOG_LEVEL, LOG_FORMAT
- DATABASE_URL, DB_ROOT
- GLM_API_KEY, GLM_API_URL (chat), GLM_EMBEDDINGS_API_URL (embeddings)
- GLM_MODEL, GLM_REQUEST_TIMEOUT, LLM_MOCK, LLM_RETRIES, LLM_BACKOFF_BASE
- GLM_EMBEDDING_MODEL, GLM_EMBEDDING_DIM, GLM_BATCH_SIZE
- SEMANTIC_DEFAULT_K, SEMANTIC_MIN_SIMILARITY, GLM_MAX_RETRIES, GLM_RETRY_DELAY, GLM_DEBUG
- EMBEDDING_CACHE_SIZE, EMBEDDING_CACHE_PERSISTENT

Notes:
- If `GLM_EMBEDDINGS_API_URL` is not set, it is derived from `GLM_API_URL` when possible, otherwise a sensible default is used.
- Avoid importing `os` to read env in service modules; rely on `get_settings()` for consistency and testability.
