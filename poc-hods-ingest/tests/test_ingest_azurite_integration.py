"""Integration test against a real Azurite blob emulator.

Exercises the real BlobServiceClient + delta-state read/write cycle end to
end; only the SharePoint/Graph-facing calls are mocked (no real tenant
needed). Requires Node/npx to spin up Azurite — auto-skipped when
unavailable so it never breaks CI (which only installs Python deps).

Run manually:
    pytest tests/test_ingest_azurite_integration.py -v
"""

import json
import shutil
import socket
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest
from azure.storage.blob import BlobServiceClient

from function_app import _get_delta_changes, _read_delta_state, _save_delta_state

AZURITE_ACCOUNT_NAME = "devstoreaccount1"
AZURITE_ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)

DELTA_STATE_BLOB = "hods-library-delta-state.json"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Azurite did not become ready on port {port} within {timeout}s")


pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None, reason="npx/Azurite not available in this environment"
)


@pytest.fixture(scope="module")
def azurite_connection_string(tmp_path_factory):
    blob_port = _free_port()
    queue_port = _free_port()
    table_port = _free_port()
    location = tmp_path_factory.mktemp("azurite-data")
    proc = subprocess.Popen(
        [
            "npx", "--yes", "azurite",
            "--silent",
            "--location", str(location),
            "--blobHost", "127.0.0.1",
            "--blobPort", str(blob_port),
            "--queuePort", str(queue_port),
            "--tablePort", str(table_port),
            "--skipApiVersionCheck",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(blob_port)
        connection_string = (
            "DefaultEndpointsProtocol=http;"
            f"AccountName={AZURITE_ACCOUNT_NAME};"
            f"AccountKey={AZURITE_ACCOUNT_KEY};"
            f"BlobEndpoint=http://127.0.0.1:{blob_port}/{AZURITE_ACCOUNT_NAME};"
        )
        yield connection_string
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def container_client(azurite_connection_string):
    blob_service_client = BlobServiceClient.from_connection_string(azurite_connection_string)
    container_name = "ingest-output-delta-test"
    cc = blob_service_client.get_container_client(container_name)
    try:
        cc.delete_container()
    except Exception:
        pass
    cc.create_container()
    yield cc


def test_save_and_read_delta_state_roundtrip(container_client):
    token = "https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=xyz"
    _save_delta_state(container_client, DELTA_STATE_BLOB, token)
    retrieved = _read_delta_state(container_client, DELTA_STATE_BLOB)
    assert retrieved == token


def test_read_delta_state_returns_none_when_missing(container_client):
    result = _read_delta_state(container_client, DELTA_STATE_BLOB)
    assert result is None


def test_overwrite_delta_state(container_client):
    _save_delta_state(container_client, DELTA_STATE_BLOB, "delta-link-v1")
    _save_delta_state(container_client, DELTA_STATE_BLOB, "delta-link-v2")
    assert _read_delta_state(container_client, DELTA_STATE_BLOB) == "delta-link-v2"


def test_get_delta_changes_single_page(container_client):
    """_get_delta_changes should return items from a single-page Graph delta response."""
    delta_items = [
        {"id": "1", "name": "a.pdf", "file": {}, "lastModifiedDateTime": "2024-06-01T00:00:00Z"},
        {"id": "2", "name": "b.pdf", "file": {}, "lastModifiedDateTime": "2024-06-02T00:00:00Z"},
    ]
    next_delta_link = "https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=next"
    delta_response = {
        "value": delta_items,
        "@odata.deltaLink": next_delta_link,
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = delta_response

    with patch("function_app.requests.get", return_value=mock_response):
        items, new_link = _get_delta_changes("https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=old", {})

    assert len(items) == 2
    assert items[0]["name"] == "a.pdf"
    assert items[1]["name"] == "b.pdf"
    assert new_link == next_delta_link


def test_get_delta_changes_multi_page(container_client):
    """_get_delta_changes should follow @odata.nextLink pagination."""
    page1 = {
        "value": [{"id": "1", "name": "a.pdf", "file": {}}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites/abc/delta?$skipToken=page2",
    }
    page2 = {
        "value": [{"id": "2", "name": "b.pdf", "file": {}}],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=final",
    }

    responses = [page1, page2]
    call_count = [0]

    def side_effect(url, headers, timeout=None):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = responses[call_count[0]]
        call_count[0] += 1
        return mock

    with patch("function_app.requests.get", side_effect=side_effect):
        items, new_link = _get_delta_changes("https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=old", {})

    assert len(items) == 2
    assert new_link == "https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=final"


def test_delta_state_persists_after_save(container_client):
    """Verify delta state blob exists and contains valid JSON after save."""
    token = "https://graph.microsoft.com/v1.0/sites/abc/delta?$deltaToken=persisted"
    _save_delta_state(container_client, DELTA_STATE_BLOB, token)

    blob_names = [b.name for b in container_client.list_blobs()]
    assert DELTA_STATE_BLOB in blob_names

    raw = container_client.get_blob_client(DELTA_STATE_BLOB).download_blob().readall().decode()
    data = json.loads(raw)
    assert data.get("delta_link") == token
