# Manual Functional / E2E Checklist

This checklist is for validating the deployed `poc-hods-ingest` Function App
against a real SharePoint site and a real Azure storage account. It cannot
be automated from CI or a sandboxed dev environment because it requires
network access to your tenant and Azure subscription — work through it by
hand in an Azure dev environment after deployment.

Scope: validates the **current** architecture (Key Vault-backed connection
string for blob storage, app-setting secrets for SharePoint — see
`infra/main.bicep`), not the not-yet-implemented Managed Identity-to-Storage
path tracked as ISSUE-7 in `docs/DEV-GUIDE-INGEST.md`.

## 1. Connectivity / permissions

- [ ] Trigger a real run per `RUNBOOK.md` Run mode B (or via the Azure
      Portal's "Test/Run" on the timer trigger) against the real
      SharePoint site and real storage account.
- [ ] Confirm no `401`/`403`/`404` errors from Graph in the Function App's
      logs or Application Insights — see the troubleshooting table in
      `RUNBOOK.md` for root causes if you hit one.
- [ ] Confirm files land in the `ingest-output` (or configured) container
      with the expected `Modified` / `Prefix` / `ContentType` blob
      metadata, and that PDF files also carry a `Purpose_and_Scope` metadata
      field where a matching section was found.
- [ ] Confirm a `hods-library-delta-state.json` blob exists in the container
      after the run completes (this replaces the old `last-sync` blob).

## 2. Identity check (current architecture)

The Function App's system-assigned managed identity should have **Key
Vault Secrets User** on the Key Vault, which is what lets it resolve the
`@Microsoft.KeyVault(SecretUri=...)` references for
`BLOB_STORAGE_CONNECTION_STRING` / `AzureWebJobsStorage` without those
secrets sitting directly in app settings (granted in
`infra/main.bicep:262-269`).

```bash
az role assignment list \
  --scope <keyVaultResourceId> \
  --query "[?principalId=='<functionApp-managed-identity-principalId>']"
```

- [ ] Confirms a role assignment for **Key Vault Secrets User**
      (`4633458b-17de-408a-b874-0445c86b69e6`) exists for the Function
      App's principal.

**Forward-looking note:** if ISSUE-7 (switching blob upload from a
connection string to Managed Identity talking directly to Storage) is
implemented later, add a check here for the **Storage Blob Data
Contributor** role on the storage account instead, and the corresponding
role-assignment query against the storage account's resource ID.

## 3. Throughput / volume (per-run cap regression check)

- [ ] Point the function at (or temporarily lower `INGEST_MAX_FILES_PER_RUN`
      against) a library with more changed files than the cap.
- [ ] Confirm the run uploads exactly the capped number of files and stops.
- [ ] Confirm the next run picks up the remaining files instead of skipping
      them — the delta-query model naturally handles this: the delta token
      records only confirmed changes, so any items not uploaded remain
      eligible on the next run.

## 4. Error handling

- [ ] Temporarily misconfigure `SHAREPOINT_SITE_PATH` (or
      `SHAREPOINT_SITE_ID`) to an invalid value and trigger a run.
- [ ] Confirm the failure is logged rather than crashing the host. Query in
      Application Insights:
      ```kusto
      traces
      | where severityLevel >= 3 and message has "Failed to sync"
      ```
- [ ] Restore the correct value and confirm a subsequent run succeeds and
      `hods-library-delta-state.json` is unaffected by the failed run (the
      delta-state blob is never updated on failure — see `Ingest()` in
      `function_app.py`).

## 5. Memory / PDF extraction validation

- [ ] Upload a large (>50 MB) PDF test file into the SharePoint library and
      trigger a sync.
- [ ] Watch the Function App's memory metric (Portal → Function App →
      Metrics, or Application Insights "Memory working set") during the
      run.
- [ ] Confirm there's no memory spike disproportionate to file size — the
      function buffers the file in memory to enable `pypdf` extraction, but
      the buffer is bounded by file size, not unbounded. For very large PDFs,
      watch for memory pressure and consider whether `INGEST_FILE_EXTENSIONS`
      should exclude them if memory is a concern.
- [ ] Confirm the uploaded blob's metadata includes a `Purpose_and_Scope`
      key if the PDF has a matching section heading.

## 6. Idempotency (spot-check in a real environment)

The Azurite-backed integration test (`tests/test_ingest_azurite_integration.py`)
already proves this against a real `BlobServiceClient` with mocked
SharePoint calls. As a real-tenant spot-check:

- [ ] Trigger two runs back-to-back with no SharePoint changes in between.
- [ ] Confirm the second run uploads 0 files (check
      "Ingestion completed. Files uploaded: 0" in the logs).

## 7. Historical load (`IngestHistorical` HTTP trigger)

These checks validate the one-off backfill trigger. Retrieve the function key
from: Function App → Functions → `IngestHistorical` → Function Keys.

### 7.1 Basic call

- [ ] POST to `/api/IngestHistorical?code=<key>` with no other parameters.
- [ ] Confirm HTTP 200 response with JSON body `{"uploaded": N, "start_date": null, "end_date": null}`.
- [ ] Confirm files appear in the `ingest-output` container with correct metadata.

### 7.2 Date-range filter

- [ ] POST to `/api/IngestHistorical?code=<key>&start_date=2024-01-01T00:00:00Z&end_date=2025-01-01T00:00:00Z`.
- [ ] Confirm only files with `Modified` metadata within the specified range are uploaded.
- [ ] Confirm the response body's `start_date` and `end_date` reflect the supplied values.

### 7.3 Delta-state isolation

- [ ] Note the current contents of `hods-library-delta-state.json` before the call
      (download it or copy its content from the Portal's Storage Browser).
- [ ] POST to `/api/IngestHistorical?code=<key>`.
- [ ] Confirm `hods-library-delta-state.json` is **unchanged** after the historical
      load completes — the blob's last-modified timestamp and content must be
      identical to before the call.
- [ ] Trigger the incremental timer (section 1) and confirm it picks up only files
      changed since the last delta sync, not a full re-scan.

### 7.4 `max_files` cap

- [ ] POST to `/api/IngestHistorical?code=<key>&max_files=5`.
- [ ] Confirm the response shows `"uploaded": 5` (or fewer if the library has
      fewer than 5 matching files) and the run stops without error.

### 7.5 Invalid parameters

- [ ] POST with `start_date=not-a-date` — confirm HTTP 400 response with an
      error message (not a 500 crash).
- [ ] POST with `end_date` earlier than `start_date` — confirm HTTP 400 response.
