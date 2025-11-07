import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(scope="session", autouse=True)
def setup_database(tmp_path_factory):
    """Configure database root into a temp directory and initialise schema."""
    root = tmp_path_factory.mktemp("db_root")
    os.environ["DB_ROOT"] = str(root)

    import app.config.database_config as db_config
    import app.database_pool as db_pool
    import app.database as database
    import app.repository.plan_storage as plan_storage
    import app.repository.plan_repository as plan_repository
    import app.repository.plan_storage as plan_storage_module

    # Reset singletons / cached globals to ensure fresh configuration
    db_config._db_config = None  # type: ignore[attr-defined]
    if getattr(db_pool, "_connection_pool", None):
        db_pool.close_connection_pool()

    importlib.reload(db_config)
    importlib.reload(db_pool)
    importlib.reload(database)
    importlib.reload(plan_storage)
    importlib.reload(plan_storage_module)
    importlib.reload(plan_repository)

    database.init_db()
    yield

    try:
        db_pool.close_connection_pool()
    except Exception:
        pass


@pytest.fixture()
def plan_repo():
    from app.repository.plan_repository import PlanRepository

    return PlanRepository()


@pytest.fixture()
def main_db_path() -> Path:
    from app.config.database_config import get_main_database_path

    return Path(get_main_database_path())


@pytest.fixture()
def sqlite_connection():
    """Utility fixture to yield a sqlite3 connection factory bound to Row rows."""

    def _connect(path: Path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return _connect
