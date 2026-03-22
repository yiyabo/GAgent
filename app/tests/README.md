# Test Layers

- Default suite: `pytest -q app/tests`
- Integration layer: `pytest -q app/tests -m integration`
- Production smoke layer: `pytest -q app/tests -m prod_smoke`

## Markers

- `integration`: real `create_app()` tests with isolated local database/filesystem dependencies
- `prod_smoke`: production-oriented startup, HTTP, and WebSocket smoke coverage
- `external`: reserved for future tests that require staging or real external services

## CI split

- PR: run everything except `prod_smoke` and `external`
- Nightly/manual: run `prod_smoke` with coverage reporting
