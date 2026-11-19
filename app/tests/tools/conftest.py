import pytest
from fastapi import FastAPI, Request

from app.services.request_principal import RequestPrincipal


@pytest.fixture
def http_tool_app(isolated_app_env):
    from app.database import init_db
    from app.routers.tool_routes import router

    init_db()
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.principal = RequestPrincipal(user_id="alice", email=None)
        return await call_next(request)

    app.include_router(router)
    return app
