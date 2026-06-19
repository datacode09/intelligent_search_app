# Intelligent Search App — HODS

A full-stack enterprise document search system powered by Azure AI Search, Azure OpenAI, and SharePoint.

## Quick start

**Read [SETUP.md](SETUP.md) — it walks you through everything from zero to a running app.**

## Components

| Folder | What it is |
|---|---|
| [`poc-hods-ingest/`](poc-hods-ingest/) | Azure Function — syncs SharePoint documents to Azure Blob Storage every hour |
| [`hydro-one-hods-api/`](hydro-one-hods-api/) | FastAPI backend — hybrid BM25 + semantic vector search with LLM query optimization |
| [`hydro-one-hods-ui/`](hydro-one-hods-ui/) | React + Vite frontend — search UI with filters, AI query cleanup, and document highlights |
| [`infra/`](infra/) | Bicep templates — deploys all Azure resources with one command |
| [`scripts/`](scripts/) | Deployment and setup scripts |
| [`azure-pipelines/`](azure-pipelines/) | Azure DevOps CI/CD pipelines for all four components |

## Architecture

```
SharePoint  →  [ingest Function]  →  Blob Storage  →  AI Search Index
                                                              ↓
                                                     [FastAPI backend]
                                                              ↓
                                                      [React frontend]
```

## One-command deploy

```powershell
az login
.\scripts\deploy.ps1 -ResourceGroup "hods-rg" -Location "eastus"
```

See [SETUP.md](SETUP.md) for the full step-by-step guide.
