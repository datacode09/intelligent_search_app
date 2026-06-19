"""Basic API tests — mock all Azure calls so no credentials needed in CI."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def mock_azure():
    with (
        patch("app.llm.DefaultAzureCredential"),
        patch("app.llm.get_bearer_token_provider", return_value=lambda: "fake-token"),
        patch("app.search_index.DefaultAzureCredential"),
        patch("app.search_index.SearchClient"),
        patch("app.telemetry.configure_telemetry"),
    ):
        yield


@pytest.fixture()
def client(mock_azure):
    from app.main import app
    return TestClient(app, raise_server_exceptions=True)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_query_requires_auth(client):
    resp = client.post("/query", json={"query": "test"})
    assert resp.status_code == 403


def test_optimize_requires_auth(client):
    resp = client.post("/OptimizeHybridQueries", json={"text": "test"})
    assert resp.status_code == 403


def test_filter_builder():
    from app.models import KeyValuePair
    from app.search_index import _build_filter

    filters = [
        KeyValuePair(key="Prefix", value="AL"),
        KeyValuePair(key="ContentType", value="Bulletins"),
    ]
    result = _build_filter(filters)
    assert "Prefix eq 'AL'" in result
    assert "ContentType/any(c: c eq 'Bulletins')" in result


def test_filter_builder_empty():
    from app.search_index import _build_filter
    assert _build_filter([]) is None


def test_filter_odata_injection():
    from app.models import KeyValuePair
    from app.search_index import _build_filter

    filters = [KeyValuePair(key="Prefix", value="O'Brien")]
    result = _build_filter(filters)
    assert "O''Brien" in result
