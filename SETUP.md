# HODS Intelligent Search App — Full Setup Guide

This guide takes you from **zero** to a fully running copy of the HODS stack in your own Azure and SharePoint environment.

---

## What you will end up with

```
Your SharePoint Documents
         ↓  (synced every hour)
Azure Blob Storage  ──→  Azure AI Search Index  (chunked + vectorized)
                                  ↓
                     hydro-one-hods-api  (FastAPI)
                                  ↓
                     hydro-one-hods-ui  (React)
                                  ↓
                           Your browser
```

---

## Prerequisites — install these first

| Tool | Why | Install link |
|---|---|---|
| Azure CLI | Deploy and manage Azure resources | https://learn.microsoft.com/en-us/cli/azure/install-azure-cli |
| Python 3.12+ | Run the API and ingest function | https://www.python.org/downloads/ |
| Node.js 20+ | Run the React UI | https://nodejs.org/ |
| Git | Already installed (you cloned this) | — |

---

## Step 1 — Create a free Azure account

> **Skip this if you already have an Azure subscription.**

1. Go to https://azure.microsoft.com/free
2. Click **Start free** and sign in with a Microsoft account (or create one)
3. You get $200 credit for 30 days — more than enough for this setup

---

## Step 2 — Log in with Azure CLI

Open PowerShell and run:

```powershell
az login
```

A browser window will open. Sign in with your Azure account. When done, run:

```powershell
az account show
```

You should see your subscription name and ID. Copy the subscription ID — you may need it later.

---

## Step 3 — Set up SharePoint

The ingest function pulls documents from a SharePoint document library. You need:
- A Microsoft 365 tenant (can be a free developer tenant — see below)
- A SharePoint site with a document library
- Two metadata columns on the library: **Prefix** and **HODSContentType**

### 3a — Get a Microsoft 365 Developer Tenant (free)

> **Skip if you already have Microsoft 365.**

1. Go to https://developer.microsoft.com/microsoft-365/dev-program
2. Click **Join now** and sign up (free)
3. You get a free Microsoft 365 E5 developer tenant with 25 user licences

### 3b — Create a SharePoint Site

1. Go to https://YOUR-TENANT.sharepoint.com (replace YOUR-TENANT with your tenant name)
2. Click **+ Create site** → **Team site**
3. Name it: `HODS Documents`
4. Note the site URL — you will need it (e.g. `https://contoso.sharepoint.com/sites/HODSDocuments`)

### 3c — Create the metadata columns

In your new SharePoint site:

1. Click **Documents** in the left navigation
2. Click **+ Add column** → **Choice**
   - Name: `Prefix`
   - Choices (add each on a new line): `AL`, `BU`, `FP`, `HO`, `PR`, `SP`
   - Click **Save**
3. Click **+ Add column** → **Managed Metadata** (or **Choice** for simplicity)
   - Name: `HODSContentType`
   - Choices: `Bulletins`, `Procedures`, `Standards`, `Guidelines`, `Forms`
   - Allow multiple values: **Yes**
   - Click **Save**

### 3d — Upload some test documents

Upload 5-10 PDF or Word documents into the Documents library. For each file, set the **Prefix** and **HODSContentType** columns.

### 3e — Register an Entra App (for the ingest function to access SharePoint)

1. Go to https://portal.azure.com → search **App registrations** → **New registration**
2. Name: `hods-ingest-app`
3. Account type: **Single tenant**
4. Click **Register**
5. Copy the **Application (client) ID** → save as `SHAREPOINT_CLIENT_ID`
6. Copy the **Directory (tenant) ID** → save as `SHAREPOINT_TENANT_ID`
7. Click **Certificates & secrets** → **New client secret**
   - Description: `hods-secret`
   - Expires: 24 months
   - Click **Add** → copy the **Value** → save as `SHAREPOINT_CLIENT_SECRET`
8. Click **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**
   - Search for and add: `Sites.Read.All`
   - Click **Add permissions**
9. Click **Grant admin consent for [your org]** → **Yes**

---

## Step 4 — Deploy Azure Infrastructure

This single script creates all Azure resources and configures them automatically.

```powershell
# From the intelligent_search_app directory:
.\scripts\deploy.ps1 -ResourceGroup "hods-rg" -Location "eastus" -Prefix "hods"
```

> **Note:** The deployment takes about 5 minutes. It will print all resource endpoints when done.

What gets created:
- Azure Storage Account + `ingest-output` container
- Azure AI Search service (Basic tier, semantic search enabled)
- Azure OpenAI service with `gpt-4o` and `text-embedding-3-large` deployments
- Azure App Service Plan (Basic B1)
- Function App for the ingest pipeline
- App Service for the API (with Managed Identity + RBAC roles pre-assigned)
- Azure AI Search index, skillset, data source, and indexer

---

## Step 5 — Add SharePoint credentials to the Function App

After Step 4, open the Azure Portal:

1. Go to your **Function App** (named `hods-ingest-...`)
2. Click **Settings** → **Environment variables**
3. Update these four settings (they were set to `REPLACE_ME` during deployment):

| Setting | Value |
|---|---|
| `SHAREPOINT_TENANT_ID` | From Step 3e |
| `SHAREPOINT_CLIENT_ID` | From Step 3e |
| `SHAREPOINT_CLIENT_SECRET` | From Step 3e |
| `SHAREPOINT_SITE_HOSTNAME` | e.g. `contoso.sharepoint.com` |
| `SHAREPOINT_SITE_PATH` | e.g. `/sites/HODSDocuments` |

4. Click **Apply** → **Confirm**

---

## Step 6 — Deploy the ingest Function App code

```powershell
cd poc-hods-ingest

# Create a Python virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Deploy to Azure (replace FUNCTION_APP_NAME with your actual name from Step 4 output)
$functionAppName = "hods-ingest-XXXXXXXX"   # from deploy.ps1 output
func azure functionapp publish $functionAppName --python
```

---

## Step 7 — Deploy the API

```powershell
cd hydro-one-hods-api

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Deploy to Azure App Service (replace API_APP_NAME with your actual name)
$apiAppName = "hods-api-XXXXXXXX"   # from deploy.ps1 output
az webapp up --name $apiAppName --resource-group hods-rg --runtime "PYTHON:3.12"
```

---

## Step 8 — Deploy the UI

```powershell
cd hydro-one-hods-ui
npm install

# Build for production
npm run build

# Deploy to Azure Static Web Apps (or just run locally for now)
# For local testing against your deployed API:
echo "VITE_API_TARGET=https://hods-api-XXXXXXXX.azurewebsites.net" > .env.local
npm run dev
```

Open http://localhost:5173 in your browser.

---

## Step 9 — Trigger the first sync

The ingest function runs every hour automatically. To trigger it immediately:

1. Azure Portal → your Function App → **Functions** → **Ingest**
2. Click **Test/Run** → **Run**

Check the logs — you should see files being synced from SharePoint to Blob Storage.

Within the next indexer run (up to 1 hour), documents will appear in Azure AI Search and become searchable in the UI.

To trigger the indexer immediately:
1. Azure Portal → your AI Search service → **Indexers** → `hods-blob-indexer`
2. Click **Run now**

---

## Running locally (development)

### API
```powershell
cd hydro-one-hods-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
az login   # needed for DefaultAzureCredential
uvicorn app.main:app --reload
# Runs at http://localhost:8000
# API docs at http://localhost:8000/docs
```

### UI
```powershell
cd hydro-one-hods-ui
npm install
npm run dev
# Runs at http://localhost:5173
# Proxies /query and /OptimizeHybridQueries to http://localhost:8000
```

### Ingest (local test)
```powershell
cd poc-hods-ingest
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Install Azurite storage emulator
npm install -g azurite
azurite --location .azurite --silent &
# Run the function
func start
```

---

## Architecture overview

| Component | Azure Resource | Cost tier |
|---|---|---|
| Document storage | Azure Blob Storage | Standard LRS (~$0.02/GB/month) |
| Search + AI ranking | Azure AI Search (Basic) | ~$75/month |
| LLM + embeddings | Azure OpenAI (S0) | Pay-per-use |
| Ingest pipeline | Azure Function App (Basic B1) | ~$13/month |
| API backend | Azure App Service (Basic B1) | ~$13/month |
| UI hosting | Azure Static Web Apps | Free tier available |

**Estimated total: ~$100-120/month** (before OpenAI usage)

---

## Troubleshooting

### "No results" in the search UI
- Check the Azure Portal → AI Search → Indexers → `hods-blob-indexer` → Last run status
- Check the Blob container has files (Portal → Storage Account → Containers → ingest-output)
- Make sure the ingest Function App ran at least once

### API returns 401 Unauthorized
- Run `az login` locally
- Verify the API App Service has the correct RBAC roles (assigned automatically by Bicep)

### Ingest function fails with Graph API error
- Double-check `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_TENANT_ID`
- Verify the Entra app has `Sites.Read.All` with admin consent granted

### OpenAI deployment not found
- The `gpt-4o` and `text-embedding-3-large` models may not be available in all regions
- If your region doesn't support them, go to the Azure Portal → your OpenAI resource → **Model deployments** and deploy available models, then update the `AZURE_OPENAI_DEPLOYMENT_NAME` env var

---

## Repository map

```
intelligent_search_app/          ← You are here (meta-repo)
├── SETUP.md                     ← This guide
├── infra/
│   ├── main.bicep               ← Azure infrastructure (IaC)
│   ├── search-index.json        ← AI Search index definition
│   └── search-indexer.json      ← Skillset + indexer definition
├── scripts/
│   ├── deploy.ps1               ← One-shot deployment script
│   └── create-indexer.py        ← Sets up AI Search pipeline
├── azure-pipelines/
│   ├── infra.yml                ← CI/CD: infrastructure
│   ├── api.yml                  ← CI/CD: FastAPI backend
│   ├── ui.yml                   ← CI/CD: React frontend
│   ├── ingest.yml               ← CI/CD: Azure Function
│   └── README.md                ← How to set up pipelines in Azure DevOps
├── poc-hods-ingest/             ← SharePoint → Blob sync (Azure Function)
├── hydro-one-hods-api/          ← Search API (FastAPI)
└── hydro-one-hods-ui/           ← Search UI (React + Vite)
```

## CI/CD with Azure DevOps

See **[azure-pipelines/README.md](azure-pipelines/README.md)** for the full setup guide.

Quick summary:
1. Import this repo into Azure Repos
2. Create a Service Connection named `hods-azure-service-connection`
3. Register all four pipelines from the `azure-pipelines/` folder
4. Set the pipeline variables (`API_APP_NAME`, `FUNCTION_APP_NAME`, `API_APP_URL`, `STATIC_WEB_APP_TOKEN`)
5. Create a `production` environment with a manual approval gate
6. Run `hods-infra` first, then the other three
