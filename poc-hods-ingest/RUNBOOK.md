# Ingest Function — Run Book

Step-by-step instructions for running `poc-hods-ingest` end-to-end on your
laptop, in two modes: a fully local emulator run, and a run against real
Azure Blob Storage. Scope: SharePoint → Blob Storage only (this does not
cover creating the Azure AI Search index/indexer — see
`scripts/create-indexer.py` and `infra/search-index.json` separately if
you need that).

## Security — read this first

- `local.settings.json` holds real secrets (your SharePoint service
  principal's client secret, and/or a real storage connection string). It
  is gitignored — only `local.settings.json.example` (placeholders) is
  committed. **Never** remove it from `.gitignore`, paste secrets into any
  other tracked file, or commit it even temporarily.
- If a client secret has ever been shared over chat, email, or a
  screenshot, treat it as compromised and rotate it in Entra ID (App
  registrations → your app → Certificates & secrets) before relying on it
  for anything beyond a quick local test.

## Prerequisites

- Python 3.13
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local) — `npm install -g azure-functions-core-tools@4`
- Node.js (for `npx azurite`, emulator mode only)
- An Entra ID app registration (service principal) with Microsoft Graph
  **application** permission `Sites.Read.All` (or a more restrictive,
  site-scoped permission) and admin consent granted. You need this in
  *both* modes below — there is no SharePoint emulator.
- The tenant ID, client ID, and client secret for that app registration.

## 1. One-time setup

```bash
cd poc-hods-ingest
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest               # only needed to run the unit tests
cp local.settings.json.example local.settings.json
```

Edit `local.settings.json` and fill in:
- `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`
- `SHAREPOINT_SITE_HOSTNAME` (e.g. `contoso.sharepoint.com`)
- `SHAREPOINT_SITE_PATH` (e.g. `/sites/HODS`)
- `SHAREPOINT_LIBRARY_DRIVE_NAME` (e.g. `Documents`)

Run the unit tests to sanity-check your environment before touching real
Azure resources:

```bash
pytest tests/ -v
```

## 2. Run mode A — local emulator (Azurite)

Good for testing the SharePoint → Blob logic without touching any real
Azure storage. Real SharePoint credentials are still required.

Leave these as-is in `local.settings.json`:
```json
"AzureWebJobsStorage": "UseDevelopmentStorage=true",
"BLOB_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
```

Start Azurite in one terminal:
```bash
# macOS/Linux
npx -y azurite --silent --location .azurite

# Windows PowerShell
if (!(Test-Path .azurite)) { New-Item -ItemType Directory .azurite | Out-Null }; $env:NODE_OPTIONS=''; npx -y azurite --location .azurite --silent
```

In a second terminal, start the function host:
```bash
func start
# If port 7071 is busy:
func start --port 7072
```

Trigger the ingest run immediately instead of waiting for the schedule:
```bash
curl -X POST http://localhost:7071/admin/functions/Ingest \
  -H "Content-Type: application/json" \
  -d "{}"
```

Verify: connect [Azure Storage Explorer](https://azure.microsoft.com/en-us/products/storage/storage-explorer)
to the local emulator (its default connection string is preconfigured),
look in the `ingest-output` container for uploaded files and a `last-sync`
blob.

## 3. Run mode B — real Azure Blob Storage

Lands files in a real storage account.

In the Azure Portal, open the target storage account → **Access keys** →
copy a connection string. Paste it into `local.settings.json` only
(never into a tracked file):
```json
"BLOB_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net",
"BLOB_CONTAINER_NAME": "ingest-output"
```

`AzureWebJobsStorage` can stay as `UseDevelopmentStorage=true` (it's only
used by the Functions host for its own bookkeeping, not by the ingest
logic) — or point it at the real account too if you'd rather avoid running
Azurite at all.

Start the function host and trigger it the same way as mode A:
```bash
func start
curl -X POST http://localhost:7071/admin/functions/Ingest -H "Content-Type: application/json" -d "{}"
```

Verify: in the Azure Portal, open the storage account → Containers →
`ingest-output`, confirm files appear with the expected `Modified` /
`Prefix` / `ContentType` blob metadata, and a `last-sync` blob exists.

## 4. Adjusting run behavior

Both are app settings in `local.settings.json`:

| Setting | Default | Use for |
|---|---|---|
| `INGEST_SCHEDULE_CRON` | `0 0 * * * *` (hourly) | Lower only for short local test windows. Running every minute against real SharePoint will trigger Graph throttling — don't use a tight schedule against production data. |
| `INGEST_MAX_FILES_PER_RUN` | `500` | Lower (e.g. `10`) to do a quick smoke test against a large library without waiting for a full sync. |

## 5. Optional — Azurite-backed idempotency integration test

`tests/test_ingest_azurite_integration.py` runs the upload-then-advance-
last-sync cycle three times in a row against a **real** `BlobServiceClient`
backed by a temporary Azurite instance (only the SharePoint/Graph calls are
mocked), and asserts the 2nd and 3rd runs upload 0 files. It needs Node/npx
to launch Azurite itself, separate from the function host's own Azurite
instance in section 2 — it starts and tears down its own instance on
throwaway ports, so it won't conflict with one you already have running.

```bash
pytest tests/test_ingest_azurite_integration.py -v
```

It's automatically skipped (not failed) if `npx` isn't on `PATH`, so it
never breaks CI, which only installs Python dependencies.

## 6. Manual functional / E2E checklist

See `E2E-CHECKLIST.md` for a checklist to work through by hand against a
real SharePoint site and real Azure storage account — covers connectivity,
managed-identity/Key-Vault permissions, the per-run file cap, error
handling, and memory/streaming behavior on large files. Cannot be
automated since it requires real tenant/subscription network access.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Graph token request fails with `401` | Wrong tenant/client ID or secret, or secret expired | Re-check `SHAREPOINT_TENANT_ID`/`_CLIENT_ID`/`_CLIENT_SECRET`; regenerate the secret in Entra ID if expired |
| `_get_graph_token` succeeds but site/drive lookup fails with `403` | App registration is missing `Sites.Read.All` application permission, or admin consent wasn't granted | In Entra ID → App registrations → your app → API permissions, add the permission and click "Grant admin consent" |
| `RuntimeError: Unable to resolve SharePoint site id` | Wrong `SHAREPOINT_SITE_HOSTNAME` or `SHAREPOINT_SITE_PATH` | Confirm the exact hostname (no `https://`) and path (starts with `/sites/...`) from the SharePoint site URL |
| `RuntimeError: Drive 'X' not found in site` | `SHAREPOINT_LIBRARY_DRIVE_NAME` doesn't match an actual document library name | Open the SharePoint site, check the exact library name (case-insensitive match is supported, but the name itself must match) |
| Repeated `429` responses from Graph | Schedule too tight, or too many files processed per run | Use the default hourly schedule; lower `INGEST_MAX_FILES_PER_RUN` |
| Blob upload fails with `AuthorizationFailure`/`AuthenticationFailed` | Wrong or expired storage connection string, or storage firewall blocking your IP | Re-copy the connection string from the Portal; check the storage account's Networking settings if you're outside an allowed network |
| `func start` says port 7071 is in use | Another process already bound to it | `func start --port 7072` |
| `ModuleNotFoundError` when running `func start` or `pytest` | Virtual environment not activated, or `pip install -r requirements.txt` not run | Re-activate the venv and reinstall |
| Function runs but uploads 0 files even though SharePoint has changes | `last-sync` blob already past the files' modified time from a previous run | Delete the `last-sync` blob from the container to force a full resync from epoch |
