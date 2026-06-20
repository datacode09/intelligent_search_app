# Development & Testing Guide — Ingest Component (`poc-hods-ingest`)

## Overview

The ingest component is a Python Azure Function (timer trigger) that polls SharePoint via the Microsoft Graph API, downloads changed documents, and uploads them with metadata to Azure Blob Storage. The AI Search indexer then picks up blobs from the container automatically.

**Technology:** Python 3.12, Azure Functions v4, `azure-functions`, `azure-storage-blob`, `requests`

---

## Local Development Setup

### 1. Prerequisites

- Python 3.12
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local) (`npm install -g azure-functions-core-tools@4`)
- [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) for local blob storage emulation (`npm install -g azurite`)

### 2. Create a virtual environment

```bash
cd poc-hods-ingest
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest pytest-cov     # test dependencies (not in requirements.txt yet)
```

### 3. Configure local settings

`local.settings.json` is already present in the repo. Fill in your dev values:

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "BLOB_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
    "BLOB_CONTAINER_NAME": "ingest-output",
    "SHAREPOINT_TENANT_ID": "<your-tenant-id>",
    "SHAREPOINT_CLIENT_ID": "<your-app-client-id>",
    "SHAREPOINT_CLIENT_SECRET": "<your-app-client-secret>",
    "SHAREPOINT_SITE_HOSTNAME": "<tenant>.sharepoint.com",
    "SHAREPOINT_SITE_PATH": "/sites/<site-name>",
    "SHAREPOINT_LIBRARY_DRIVE_NAME": "Documents"
  }
}
```

> **Never commit secrets.** `local.settings.json` is already listed in `.gitignore`.

### 4. Start Azurite (local blob emulator)

```bash
azurite --silent --location /tmp/azurite --debug /tmp/azurite-debug.log
```

Create the container before running the function:

```bash
az storage container create --name ingest-output --connection-string "UseDevelopmentStorage=true"
```

### 5. Run the function locally

```bash
func start
```

The timer trigger fires on its CRON schedule (`0 0 * * * *` = hourly, or adjust in `function_app.py` for local dev). To trigger it immediately without waiting:

```bash
curl -X POST http://localhost:7071/admin/functions/hods_ingest_timer \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 6. Known issues to fix before running

| Issue | File | Fix |
|---|---|---|
| ISSUE-1: timer fires every minute | `function_app.py` | Change schedule to `"0 0 * * * *"` |
| ISSUE-2: `max_files=5` cap | `function_app.py` | Set via `INGEST_MAX_FILES_PER_RUN` env var |
| ISSUE-7: connection string auth | `function_app.py` | Replace with `DefaultAzureCredential` |

See the TODO comments in `function_app.py` for exact fix snippets.

---

## Project Structure

```
poc-hods-ingest/
├── function_app.py          # Main function: timer trigger + all ingest logic
├── host.json                # Azure Functions host config
├── local.settings.json      # Local env vars (gitignored)
├── requirements.txt         # Runtime dependencies
└── tests/
    ├── __init__.py
    └── test_ingest.py       # 11 unit tests for pure helper functions
```

### Key functions in `function_app.py`

| Function | Purpose |
|---|---|
| `hods_ingest_timer` | Timer trigger entry point |
| `get_access_token()` | OAuth2 client-credentials → Graph API token |
| `get_sharepoint_documents()` | Graph API list query with `$filter` on Modified |
| `_upload_changed_files()` | Download + upload loop with last-sync tracking |
| `download_file()` | Fetches document bytes via `/content` endpoint |
| `upload_to_blob()` | Writes bytes + metadata dict to Blob Storage |
| `_parse_last_sync()` | Parses ISO8601 timestamp from `last_sync.txt` |
| `_to_blob_name()` | Sanitises filenames (strips paths, replaces spaces) |
| `_to_blob_metadata_value()` | Encodes SharePoint LookupValue fields as ASCII strings |

---

## Running Tests

```bash
cd poc-hods-ingest
pytest tests/ -v
```

All 11 tests cover pure helper functions — no Azure credentials needed.

```bash
# With coverage report
pytest tests/ -v --cov=function_app --cov-report=term-missing
```

### What is tested

| Test class | Covers |
|---|---|
| `TestParseLastSync` | 6 cases: None, empty string, ISO8601-Z, ISO8601-offset, legacy format, bad input |
| `TestToBlobName` | 4 cases: simple name, path stripping, space replacement, empty input |
| `TestToBlobMetadataValue` | 5 cases: string, None, list of LookupValues, single dict, non-ASCII stripping |

### What is NOT yet tested (gaps)

- `get_access_token()` — requires mocking `requests.post` to a token endpoint
- `get_sharepoint_documents()` — requires mocking Graph API paginated responses
- `upload_to_blob()` — requires mocking `BlobServiceClient`
- Incremental sync logic (last-sync file read/write round-trip)

To add these, use `unittest.mock.patch` on `requests.post` and `azure.storage.blob.BlobServiceClient`.

---

## Linting

`ruff.toml` is not yet present (ISSUE-27 in task tracker). To add it:

```toml
# poc-hods-ingest/ruff.toml
target-version = "py312"
line-length = 100
select = ["E", "F", "W", "I", "UP", "B", "S"]
```

Run:

```bash
pip install ruff
ruff check function_app.py tests/
ruff format function_app.py tests/
```

---

## Deployment

The function is deployed via `azure-pipelines/ingest.yml`. Manual deployment:

```bash
func azure functionapp publish <function-app-name> --python
```

After deployment, verify the function appears in the Azure Portal under **Function App → Functions** and the timer trigger is shown as **Enabled**.
