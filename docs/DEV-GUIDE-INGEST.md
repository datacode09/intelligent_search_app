# Development & Testing Guide — Ingest Component (`poc-hods-ingest`)

## Overview

The ingest component is a Python Azure Function (timer trigger) that polls SharePoint via the Microsoft Graph API, downloads changed documents, and uploads them with metadata to Azure Blob Storage. The AI Search indexer then picks up blobs from the container automatically.

**Technology:** Python 3.13, Azure Functions v4, `azure-functions`, `azure-storage-blob`, `requests`

---

## Local Development Setup

### 1. Prerequisites

- Python 3.13
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local) (`npm install -g azure-functions-core-tools@4`)
- [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) for local blob storage emulation (`npm install -g azurite`)
- An Entra ID app registration (service principal) with Microsoft Graph application permission `Sites.Read.All` (admin consent granted) — needed in every run mode, since SharePoint itself is never emulated. See `poc-hods-ingest/RUNBOOK.md` for full setup and troubleshooting.

### 2. Create a virtual environment

```bash
cd poc-hods-ingest
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest pytest-cov     # test dependencies (not in requirements.txt yet)
```

### 3. Configure local settings

Copy `poc-hods-ingest/local.settings.json.example` to `poc-hods-ingest/local.settings.json` and fill in your dev values:

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
    "SHAREPOINT_LIBRARY_DRIVE_NAME": "Documents",
    "INGEST_SCHEDULE_CRON": "0 0 * * * *",
    "INGEST_MAX_FILES_PER_RUN": "500"
  }
}
```

> **Never commit secrets.** `local.settings.json` is listed in `.gitignore`; only the placeholder `local.settings.json.example` is tracked.

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

The timer trigger fires on the schedule in `INGEST_SCHEDULE_CRON` (defaults to `0 0 * * * *`, hourly). To trigger it immediately without waiting:

```bash
curl -X POST http://localhost:7071/admin/functions/Ingest \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 6. Known issues

| Issue | Status | File |
|---|---|---|
| ISSUE-1: timer fired every minute | Fixed — schedule now configurable via `INGEST_SCHEDULE_CRON`, defaults to hourly | `function_app.py` |
| ISSUE-2: `max_files=5` hardcoded cap | Fixed — configurable via `INGEST_MAX_FILES_PER_RUN`, defaults to 500 | `function_app.py` |
| ISSUE-3: last-sync advanced to now even on partial failure | Fixed — last-sync now advances only to the earliest modified time among successfully uploaded files | `function_app.py` |
| ISSUE-7: connection string auth instead of Managed Identity | Open | `function_app.py` |

See the remaining TODO comment in `function_app.py` for the ISSUE-7 fix snippet.

---

## Project Structure

```
poc-hods-ingest/
├── function_app.py                 # Main function: timer trigger + all ingest logic
├── host.json                       # Azure Functions host config
├── local.settings.json.example     # Template for local env vars (copy to local.settings.json, gitignored)
├── RUNBOOK.md                      # Step-by-step run + troubleshooting guide
├── requirements.txt                # Runtime dependencies
└── tests/
    ├── __init__.py
    └── test_ingest.py              # Unit tests for pure helper functions + upload batching logic
```

### Key functions in `function_app.py`

| Function | Purpose |
|---|---|
| `Ingest()` | Timer trigger entry point |
| `_get_graph_token()` | OAuth2 client-credentials → Graph API token |
| `_resolve_site_id()` / `_get_site_id()` | Resolve the SharePoint site ID via Graph |
| `_get_drive_id()` | Find the document library drive by name |
| `_list_all_items()` | Breadth-first listing of all drive items (handles nested folders) |
| `_upload_changed_files()` | Download + upload loop; returns count uploaded and earliest successfully-synced modified time |
| `_fetch_item_fields()` | Fetch SharePoint list-item fields (e.g. lookup columns) via Graph |
| `_parse_last_sync()` | Parses the last-sync timestamp (ISO8601 or legacy format) |
| `_to_blob_name()` | Sanitises filenames (strips paths, replaces spaces) |
| `_to_blob_metadata_value()` | Encodes SharePoint LookupValue fields as ASCII strings |

---

## Running Tests

```bash
cd poc-hods-ingest
pytest tests/ -v
```

All tests cover pure helper functions and the upload-batching logic with mocked Graph/Blob calls — no real Azure credentials needed.

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
| `TestUploadChangedFiles` | 3 cases: an item failure propagates rather than silently returning a partial result, `max_files` caps uploads and reports the earliest successful modified time, default cap allows more than 5 files |

### What is NOT yet tested (gaps)

- `_get_graph_token()` — requires mocking `requests.post` to a token endpoint
- `_get_site_id()` / `_get_drive_id()` — requires mocking paginated Graph responses
- Full blob upload round-trip via a real or fake `BlobServiceClient` (current tests mock it)
- Incremental sync logic end-to-end (last-sync blob read/write round-trip against Azurite)

To add these, use `unittest.mock.patch` on `requests.post`/`requests.get` and `azure.storage.blob.BlobServiceClient`.

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
