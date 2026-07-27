# poc-hods-ingest

## Settings

These settings must be updated in the local.settings.json file when running locally. Some settings, those with a value of
Yes in the 'Needs to be added to Azure Function App' column below, must be added to the Function App in Azure under 
Settings / Environment variables / App settings.

| Setting name | Setting value | Needs to be added to Azure Function App |
| ------------ | ------------- | --------------------------------------- |
| FUNCTIONS_WORKER_RUNTIME | python | No |
| AzureWebJobsStorage | UseDevelopmentStorage=true when running locally | No; use the pre-defined value in Azure |
| BLOB_STORAGE_CONNECTION_STRING | DefaultEndpointsProtocol=https;AccountName=<account-name>;AccountKey=<account-key>;EndpointSuffix=core.windows.net | Yes. Whether running locally or in Azure, replace <account-name> and <account-key>. |
| BLOB_CONTAINER_NAME | Name of container where files should be written, e.g. ingest-output | Yes |
| SHAREPOINT_TENANT_ID | Tenant id of the service principal used to connect to SharePoint | Yes |
| SHAREPOINT_CLIENT_ID | Client id of the service principal used to connect to SharePoint | Yes |
| SHAREPOINT_CLIENT_SECRET | Secret for the service principal used to connect to SharePoint | Yes |
| SHAREPOINT_SITE_HOSTNAME | Hostname of the SharePoint site (e.g. contoso.sharepoint.com) | Yes |
| SHAREPOINT_SITE_PATH | /sites/YourSiteName | Yes |
| SHAREPOINT_SITE_ID | Optional. Pre-resolved SharePoint site ID. If set, the `_get_site_id` Graph call is skipped on every run. Leave unset unless you have a specific reason to hard-code it. | No |
| SHAREPOINT_LIBRARY_DRIVE_NAME | Documents | Yes |
| INGEST_SCHEDULE_CRON | NCronTab expression for the timer trigger. Defaults to `0 0 * * * *` (top of every hour). Use a tighter value like `0 */2 * * * *` only for short local test runs — running every minute against real SharePoint will hit MS Graph throttling (HTTP 429). | Yes |
| INGEST_MAX_FILES_PER_RUN | Max number of changed files processed in a single incremental run. Defaults to 100. Lower this (e.g. 10) for a quick local smoke test. | Yes |
| INGEST_FILE_EXTENSIONS | Comma-separated file extensions to ingest, e.g. `.pdf,.docx`. Defaults to `.pdf`. Use `**` to ingest every file type regardless of extension. | No |
| INGEST_METADATA_COLUMNS | Comma-separated list of SharePoint internal column names to include as blob metadata (e.g. `Prefix,HODSContentType`). When unset, all non-system columns are written. | No |
| INGEST_START_DATE | ISO-8601 timestamp (e.g. `2024-01-01T00:00:00Z`). Only consulted on the very first run, before a delta-state blob exists — limits how far back the initial seed scan goes. Defaults to the current time if unset (ingests only files modified after deployment). Has no effect once a delta-state blob exists. | No |
| DELTA_STATE_BLOB_NAME | Name of the blob used to persist the Microsoft Graph delta-query token for incremental sync. Defaults to `hods-library-delta-state.json`. Rarely needs changing unless multiple ingest configurations write to the same container. | No |
| HISTORICAL_MAX_FILES | Maximum number of files processed by a single `IngestHistorical` HTTP call. Defaults to 5000. The per-request `max_files` query parameter takes precedence if supplied and is lower. | No |

## Description

This Azure Function App syncs SharePoint documents into Azure Blob Storage in two modes:

**Incremental sync (timer trigger — `Ingest`):** wakes up on the `INGEST_SCHEDULE_CRON` schedule (hourly by default), fetches only files changed since the last run using a Microsoft Graph delta query, and uploads them to `BLOB_CONTAINER_NAME`. Progress is tracked in a JSON blob named `DELTA_STATE_BLOB_NAME` (`hods-library-delta-state.json` by default). On the very first run (no delta-state blob yet), the function seeds from recent SharePoint list items modified since `INGEST_START_DATE` (or since now if unset), then records a delta baseline so subsequent runs fetch only incremental changes. Up to `INGEST_MAX_FILES_PER_RUN` files are processed per run.

**Historical load (HTTP trigger — `IngestHistorical`):** a one-off HTTP POST to `/api/IngestHistorical` (function-key auth required) that scans the full SharePoint library and uploads matching files. Optional query parameters `start_date` and `end_date` (ISO-8601) narrow the date range; `max_files` caps the result for that request. This trigger never writes to the delta-state blob, so historical loads never interfere with the ongoing incremental sync.

For every file uploaded, blob metadata is populated dynamically from all non-system SharePoint columns. PDF files additionally have their "Purpose and Scope" section extracted (first 5 pages) and stored in the `Purpose_and_Scope` metadata field.

Fill in the SharePoint app settings in `local.settings.json` and ensure your Entra app has Graph application permissions (typically `Sites.Read.All`, or a more restrictive site-scoped permission), then run the function host.

## Requirements

- Python 3.13
- Visual Studio Code
- Azure Function Core Tools (https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local?pivots=programming-language-python&tabs=windows%2Cisolated-process%2Cnode-v4%2Cpython-v2%2Chttp-trigger%2Ccontainer-apps#install-the-azure-functions-core-tools)
- Azurite storage emulator (https://learn.microsoft.com/en-us/azure/storage/common/storage-install-azurite?toc=%2Fazure%2Fstorage%2Fblobs%2Ftoc.json&bc=%2Fazure%2Fstorage%2Fblobs%2Fbreadcrumb%2Ftoc.json&tabs=visual-studio%2Cblob-storage)
- An Entra ID app registration (service principal) with Microsoft Graph **application** permission `Sites.Read.All` (or more restrictive, site-scoped permission), with admin consent granted. This is required in both run modes below — there is no SharePoint emulator.

# Setup

- Setup and activate a Python virtual environment.
- Install requirements
  - pip install -r requirements.txt
- Change to the directory with the code.
- Copy `local.settings.json.example` to `local.settings.json` and fill in the SharePoint settings with your own service principal's tenant id, client id, and client secret. `local.settings.json` is gitignored — **never commit real secret values** or remove it from `.gitignore`.

## Run mode 1: local emulator (Azurite) for blob storage

Use this to exercise the SharePoint → Blob logic without touching any real Azure storage account. You still need real SharePoint SPN credentials since SharePoint itself isn't emulated.

- Leave `BLOB_STORAGE_CONNECTION_STRING` as `UseDevelopmentStorage=true` (and `AzureWebJobsStorage` the same) in `local.settings.json`.
- On Windows in a PowerShell terminal
  - if (!(Test-Path .azurite)) { New-Item -ItemType Directory .azurite | Out-Null }; $env:NODE_OPTIONS=''; npx -y azurite --location .azurite --silent
	- func start
  - If the above command says port 7071 is busy, then use
  - func start --port 7072
- See `RUNBOOK.md` for how to trigger the function immediately instead of waiting for the schedule, and how to verify uploads with Azure Storage Explorer pointed at Azurite.

## Run mode 2: real Azure storage account

Use this to actually land files in a real Storage Account (e.g. one of the `azsthodsaidevcae*` accounts).

- In the Azure Portal, open the target storage account → Access keys → copy a connection string.
- Set `BLOB_STORAGE_CONNECTION_STRING` in `local.settings.json` to that value (paste it only into this local, gitignored file — never into a tracked file or commit).
- Set `BLOB_CONTAINER_NAME` to the target container (e.g. `ingest-output`); the function creates it if it doesn't exist.
- Run `func start` as above.

See `RUNBOOK.md` for the full step-by-step, verification steps, and a troubleshooting table for common errors (Graph auth failures, SharePoint site/drive not found, throttling, blob upload errors).