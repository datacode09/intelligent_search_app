# Development & Testing Guide — Ingest Component (`poc-hods-ingest`)

## Overview

The ingest component is a Python Azure Function app with two triggers:

- **`Ingest` (timer trigger):** wakes on a schedule (hourly by default), fetches files changed since the last run via a Microsoft Graph delta query, and uploads them with metadata to Azure Blob Storage. Progress is tracked in a delta-state JSON blob.
- **`IngestHistorical` (HTTP trigger):** one-off `POST /api/IngestHistorical` endpoint for backfilling historical data. Scans the full SharePoint library (optionally filtered by date range) and uploads matching files without touching the delta-state blob.

The AI Search indexer then picks up blobs from the container automatically.

**Technology:** Python 3.13, Azure Functions v4, `azure-functions`, `azure-storage-blob`, `requests`, `pypdf>=4.0.0`

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
pip install pytest pytest-cov     # test dependencies
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
    "INGEST_MAX_FILES_PER_RUN": "100",
    "INGEST_FILE_EXTENSIONS": ".pdf",
    "DELTA_STATE_BLOB_NAME": "hods-library-delta-state.json",
    "HISTORICAL_MAX_FILES": "5000"
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

To trigger the historical load locally (no auth key required when running locally):

```bash
# Full scan
curl -X POST http://localhost:7071/api/IngestHistorical

# Date-range filter
curl -X POST "http://localhost:7071/api/IngestHistorical?start_date=2024-01-01T00:00:00Z&end_date=2025-01-01T00:00:00Z"
```

### 6. Known issues

| Issue | Status | File |
|---|---|---|
| ISSUE-1: timer fired every minute | Fixed — schedule now configurable via `INGEST_SCHEDULE_CRON`, defaults to hourly | `function_app.py` |
| ISSUE-2: `max_files=5` hardcoded cap | Fixed — configurable via `INGEST_MAX_FILES_PER_RUN`, defaults to 100 | `function_app.py` |
| ISSUE-3: last-sync advanced to now even on partial failure | Fixed — replaced by delta-query model; delta token only advances after a successful full-page fetch | `function_app.py` |
| ISSUE-7: connection string auth instead of Managed Identity | Open — blob upload still uses `BLOB_STORAGE_CONNECTION_STRING`; see TODO comment in `function_app.py` for the Managed Identity snippet | `function_app.py` |

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
| `Ingest()` | Timer trigger entry point — incremental delta-query sync |
| `IngestHistorical()` | HTTP trigger entry point — one-off historical backfill |
| `_get_graph_token()` | OAuth2 client-credentials → Graph API token |
| `_resolve_site_id()` / `_get_site_id()` | Resolve the SharePoint site ID via Graph |
| `_get_drive_id()` | Find the document library drive by name |
| `_get_drive_list_id()` | Resolve the SharePoint list ID for a drive (used for metadata lookups) |
| `_list_all_items()` | Breadth-first listing of all drive items (legacy; used by `_upload_changed_files`) |
| `_list_recent_files_from_hods_list()` | Query SP list items modified since a cutoff (used by `Ingest` on first run) |
| `_list_all_files_from_hods_list()` | Full library scan with optional date-range filter (used by `IngestHistorical`) |
| `_get_delta_changes()` | Fetch incremental changes from a Graph delta link |
| `_initialize_delta_tracking_latest()` | Capture a fresh delta baseline after the initial seed |
| `_read_delta_state()` | Read the stored delta token from blob storage |
| `_save_delta_state()` | Persist the refreshed delta token to blob storage |
| `_upload_drive_items()` | Download + upload loop for a list of drive items; calls `_build_blob_metadata` and `_extract_purpose_and_scope` |
| `_upload_changed_files()` | Legacy download + upload loop (used by older tests; not called by `Ingest`) |
| `_build_blob_metadata()` | Assemble the full blob metadata dict for a single file |
| `_fetch_item_fields()` | Fetch SharePoint list-item fields (e.g. lookup columns) via Graph |
| `_fetch_content()` | Download file bytes into memory (enables PDF extraction before upload) |
| `_extract_purpose_and_scope()` | Extract the "Purpose and Scope" section from PDF bytes via `pypdf`; falls back to PDF title |
| `_get_lookup_column_info()` / `_get_lookup_item_display_value()` | Resolve lookup column values to human-readable text |
| `_parse_last_sync()` | Parses a last-sync/modified timestamp (ISO8601 or legacy format) |
| `_parse_historical_date_param()` | Safe ISO-8601 date parsing for HTTP query parameters |
| `_to_blob_name()` | Sanitises filenames (strips paths, replaces special characters) |
| `_to_blob_metadata_value()` | Encodes SharePoint field values (LookupValue dicts, lists, taxonomy) as ASCII strings |
| `_trim_metadata()` | Enforces the 8 KB Azure blob metadata limit by dropping the longest values first |
| `_is_system_field()` | Filters out internal SharePoint fields that should not be written as metadata |
| `_sanitize_metadata_key()` | Replaces non-identifier characters in column names with `_` |
| `_get_allowed_extensions()` | Reads `INGEST_FILE_EXTENSIONS` and returns the allowed set |
| `_is_allowed_file_name()` | Checks whether a filename matches the allowed extension set |
| `_ensure_container()` | Creates the blob container if it doesn't already exist |
| `_retry()` | Retries a callable on transient errors with exponential back-off |

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
| `TestParseLastSync` | None, empty string, ISO8601-Z, ISO8601-offset, legacy format, bad input |
| `TestToBlobName` | Simple name, path stripping, space replacement, empty input |
| `TestToBlobMetadataValue` | String, None, list of LookupValues, single dict, taxonomy dict, non-ASCII stripping |
| `TestTrimMetadata` | Total size enforcement, always-keeps-Modified, drops longest first |
| `TestSanitizeMetadataKey` | Space, hyphen, special character replacement |
| `TestIsSystemField` | Known system fields excluded, user columns included |
| `TestRetry` | Retries on transient errors, raises after max attempts, succeeds on first try |
| `TestToUtcIso` | UTC and aware datetime formatting |
| `TestGetAllowedExtensions` | Default `.pdf`, explicit list, `**` wildcard |
| `TestIsAllowedFileName` | Extension matching, wildcard, case-insensitivity |
| `TestReadDeltaState` | Missing blob returns None, existing blob returns token string |
| `TestSaveDeltaState` | Writes correct JSON blob with expected fields |
| `TestGetDeltaChanges` | Single page, multi-page pagination, delta link extraction |
| `TestUploadDriveItems` | Upload count, metadata written, PDF extraction called, extension filtering |
| `TestBuildBlobMetadata` | Prefix lookup resolution, HODSContentType mapping, Modified always present |
| `TestFetchContent` | Successful download returns bytes, non-200 raises |
| `TestExtractPurposeAndScope` | Heading found, heading not found returns None, multi-page, fallback to PDF title |
| `TestDownloadAndUpload` | Streaming upload, retry on failure |
| `TestUploadChangedFiles` | Item failure propagates, `max_files` cap, default cap |
| `TestDynamicMetadataInUpload` | Non-system columns written dynamically |
| `TestPrefixLookupFallback` | Fallback when lookup resolution fails |
| `TestListAllFilesFromHodsList` | Full scan, date-range filter, extension filter, max_files cap |
| `TestParseHistoricalDateParam` | Valid ISO-8601, invalid string, None input |
| `TestIngestHistoricalEndpoint` | Success response shape, date params, max_files param, bad date returns 400 |

### What is NOT yet tested (gaps)

- `_get_graph_token()` — requires mocking `requests.post` to a token endpoint
- `_get_site_id()` / `_get_drive_id()` — requires mocking paginated Graph responses
- Full blob upload round-trip via a real `BlobServiceClient` (current tests mock it)
- Delta-state end-to-end round-trip against Azurite (covered partially by `test_ingest_azurite_integration.py`)

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
