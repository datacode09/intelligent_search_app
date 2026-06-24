"""Integration test against a real Azurite blob emulator.

Exercises the real BlobServiceClient + last-sync read/write cycle end to
end; only the SharePoint/Graph-facing calls are mocked (no real tenant
needed). Requires Node/npx to spin up Azurite — auto-skipped when
unavailable so it never breaks CI (which only installs Python deps).

Run manually:
    pytest tests/test_ingest_azurite_integration.py -v
"""

import datetime
import shutil
import socket
import subprocess
import time
from unittest.mock import patch

import pytest
from azure.storage.blob import BlobServiceClient

from function_app import _parse_last_sync, _upload_changed_files

AZURITE_ACCOUNT_NAME = "devstoreaccount1"
AZURITE_ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)


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


def _make_item(item_id, name, modified):
    return {"id": item_id, "name": name, "file": {}, "lastModifiedDateTime": modified}


def _run_sync_cycle(blob_service_client, container_name, items):
    """Mirrors the body of Ingest(): read last-sync, upload changed files,
    advance last-sync to the earliest successfully uploaded file's modified
    time (or now if everything succeeded)."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    try:
        last_sync_blob_client = blob_service_client.get_blob_client(container=container_name, blob="last-sync")
        last_sync_raw = last_sync_blob_client.download_blob().readall().decode("utf-8")
    except Exception:
        last_sync_raw = None

    last_sync = _parse_last_sync(last_sync_raw)

    with patch("function_app._get_drive_list_id", return_value="list-1"), \
         patch("function_app._get_lookup_column_info", return_value=None), \
         patch("function_app._list_all_items", return_value=items), \
         patch("function_app._fetch_item_fields", return_value={}), \
         patch("function_app.requests.get") as mock_get:
        response = mock_get.return_value
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.raise_for_status = lambda: None
        response.iter_content.side_effect = lambda *a, **k: iter([b"file-bytes"])

        uploaded, earliest_success = _upload_changed_files(
            blob_service_client=blob_service_client,
            container_name=container_name,
            drive_id="drive-1",
            site_id="site-1",
            last_sync=last_sync,
            headers={},
        )

    last_sync_time = (earliest_success or now_utc).isoformat()
    config_blob_client = blob_service_client.get_blob_client(container=container_name, blob="last-sync")
    config_blob_client.upload_blob(last_sync_time, overwrite=True)

    return uploaded


def test_idempotent_sync_three_runs(azurite_connection_string):
    container_name = "ingest-output-test"
    blob_service_client = BlobServiceClient.from_connection_string(azurite_connection_string)
    container_client = blob_service_client.get_container_client(container_name)
    try:
        container_client.delete_container()
    except Exception:
        pass
    container_client.create_container()

    items = [
        _make_item("1", "a.pdf", "2024-06-01T00:00:00Z"),
        _make_item("2", "b.pdf", "2024-06-02T00:00:00Z"),
        _make_item("3", "c.pdf", "2024-06-03T00:00:00Z"),
    ]

    uploaded_run_1 = _run_sync_cycle(blob_service_client, container_name, items)
    uploaded_run_2 = _run_sync_cycle(blob_service_client, container_name, items)
    uploaded_run_3 = _run_sync_cycle(blob_service_client, container_name, items)

    assert uploaded_run_1 == len(items)
    assert uploaded_run_2 == 0
    assert uploaded_run_3 == 0

    blob_names = sorted(b.name for b in container_client.list_blobs())
    assert blob_names == ["a.pdf", "b.pdf", "c.pdf", "last-sync"]
