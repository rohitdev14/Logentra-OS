from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logbook_server.app import create_app
from logbook_server.config import Settings


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path}/test.db"


@pytest.fixture
def settings(db_url) -> Settings:
    return Settings(database_url=db_url, open_browser=False, api_key="", host="127.0.0.1", port=8787)


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(db_url) -> TestClient:
    settings = Settings(database_url=db_url, open_browser=False, api_key="test-key-123")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
