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
      metadata, and that a `last-sync` blob exists afterward.

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
      them — this is the regression check for the last-sync-advancement
      fix (see `_upload_changed_files` in `function_app.py`: last-sync only
      advances to the earliest uploaded file's time when the cap is hit,
      so leftover files stay eligible for the next run).

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
      `last-sync` is unaffected by the failed run (it's never written on
      failure — see `Ingest()` in `function_app.py`).

## 5. Memory / streaming validation

- [ ] Upload a large (>50 MB) test file into the SharePoint library and
      trigger a sync.
- [ ] Watch the Function App's memory metric (Portal → Function App →
      Metrics, or Application Insights "Memory working set") during the
      run.
- [ ] Confirm there's no memory spike proportional to the file size —
      `_download_and_upload` streams the Graph download directly into the
      blob upload in 4 MB chunks rather than buffering the whole file, so
      memory use should stay roughly flat regardless of file size.

## 6. Idempotency (spot-check in a real environment)

The Azurite-backed integration test (`tests/test_ingest_azurite_integration.py`)
already proves this against a real `BlobServiceClient` with mocked
SharePoint calls. As a real-tenant spot-check:

- [ ] Trigger two runs back-to-back with no SharePoint changes in between.
- [ ] Confirm the second run uploads 0 files (check
      "Completed SharePoint sync. Files uploaded: 0" in the logs).
