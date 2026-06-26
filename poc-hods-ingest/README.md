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
| SHAREPOINT_LIBRARY_DRIVE_NAME | Documents | Yes |
| SHAREPOINT_METADATA_COLUMN | Name of a single SharePoint column to copy as a metadata value on the blob | Yes | 
| BLOB_METADATA_KEY | Name of the blob metadata key to hold the SharePoint column value. If not provided, the SHAREPOINT_METADATA_COLUMN name will be used | Yes |
| INGEST_SCHEDULE_CRON | NCronTab expression for the timer trigger. Defaults to `0 0 * * * *` (top of every hour). Use a tighter value like `0 */2 * * * *` only for short local test runs — running every minute against real SharePoint will hit MS Graph throttling (HTTP 429). | Yes |
| INGEST_MAX_FILES_PER_RUN | Max number of changed files uploaded in a single run. Defaults to 500. Lower this (e.g. 10) for a quick local smoke test. | Yes |
| INGEST_START_DATE | ISO-8601 timestamp (e.g. 2024-01-01T00:00:00Z). Only used on the very first run, before a last-sync blob exists — sets the starting point so a fresh deployment doesn't ingest every file ever modified. Defaults to the Unix epoch (ingests everything) if unset. No effect once a last-sync blob exists. | No |

## Description

This Azure Function App pulls changed SharePoint files (using the lastModifiedDateTime) since the value contained in a blob named 'last-sync' in the Storage Account. Up to
INGEST_MAX_FILES_PER_RUN files are then copied from SharePoint to the Storage Account and stored in BLOB_CONTAINER_NAME. The app then updates the last-sync blob with
the modified time of the earliest file it successfully uploaded in that run (or the current time if every changed file was uploaded), so any files left over when the
per-run cap is hit are picked up on the next run instead of being skipped.
	
Fill in the SharePoint app settings in local.settings.json and ensure your Entra app has Graph application permissions (typically Sites.Read.All, 
or permissions set at a more restrictive level), then run the function host.

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