# poc-hods-ingest — SharePoint → Blob Ingestion

A standalone Azure Functions app that syncs files from a SharePoint
document library into Azure Blob Storage on a timer.

## Where to look

| Doc | What's in it |
|---|---|
| [`poc-hods-ingest/README.md`](poc-hods-ingest/README.md) | Required settings (app settings / `local.settings.json`) and a description of the sync logic |
| [`poc-hods-ingest/RUNBOOK.md`](poc-hods-ingest/RUNBOOK.md) | Step-by-step guide: one-time setup, desktop testing, and cloud (Azure) testing — written for someone with zero prior Azure experience |
| [`poc-hods-ingest/E2E-CHECKLIST.md`](poc-hods-ingest/E2E-CHECKLIST.md) | Manual validation checklist for a deployed instance |

## Architecture

```
SharePoint document library  →  [Ingest Azure Function]  →  Azure Blob Storage
```

The function wakes up on a timer (hourly by default), lists files in
SharePoint changed since the last run, and uploads them to a blob
container, tracking progress in a `last-sync` blob so re-runs only pick up
new or changed files.

## CI/CD

[`azure-pipelines/ingest.yml`](azure-pipelines/ingest.yml) lints, tests,
and deploys this function. See
[`azure-pipelines/README.md`](azure-pipelines/README.md) for setup.

## Infrastructure

[`infra/main.bicep`](infra/main.bicep) provisions this function's Azure
resources (Storage, Key Vault, App Insights, Function App), alongside some
resources for other parts of the original HODS project this repo was
trimmed from — those are unused by this app and not covered by the docs
above.
