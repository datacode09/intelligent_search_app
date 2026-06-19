# Azure DevOps — Pipeline Setup Guide

Four pipelines, one per component. Each runs lint + tests on every PR and deploys on merge to `main`.

---

## Step 1 — Import this repo into Azure DevOps

1. Go to https://dev.azure.com and sign in (create a free account if needed)
2. Create a new **Organization** and a **Project** (e.g. `hods`)
3. Go to **Repos** → **Import a repository**
   - Source: `https://github.com/YOUR_USERNAME/intelligent_search_app` (or push directly)
4. After import, your code lives in Azure Repos

---

## Step 2 — Create an Azure Service Connection

This lets pipelines deploy to your Azure subscription without storing passwords.

1. **Project Settings** → **Service connections** → **New service connection**
2. Select **Azure Resource Manager** → **Workload Identity Federation (automatic)**
3. Scope: **Subscription** → select your subscription → Resource group: `hods-rg`
4. Name it exactly: **`hods-azure-service-connection`** (matches the `serviceConnection` variable in every pipeline)
5. Check **Grant access permission to all pipelines** → **Save**

---

## Step 3 — Create the four pipelines

For each pipeline file below, repeat these steps:

1. **Pipelines** → **New pipeline**
2. Where is your code? → **Azure Repos Git** → select your repo
3. Configure: **Existing Azure Pipelines YAML file**
4. Select the path to the pipeline file → **Continue** → **Save** (don't run yet)

| Pipeline name | YAML file |
|---|---|
| `hods-infra` | `azure-pipelines/infra.yml` |
| `hods-api` | `azure-pipelines/api.yml` |
| `hods-ui` | `azure-pipelines/ui.yml` |
| `hods-ingest` | `azure-pipelines/ingest.yml` |

---

## Step 4 — Set pipeline variables

### Variables shared across pipelines
Set these on **each pipeline** under **Edit → Variables**:

| Variable | Value | Secret? |
|---|---|---|
| `API_APP_NAME` | Output of `deploy.ps1` — `apiAppName` | No |
| `FUNCTION_APP_NAME` | Output of `deploy.ps1` — `functionAppName` | No |
| `API_APP_URL` | Output of `deploy.ps1` — `apiAppUrl` (e.g. `https://hods-api-xxx.azurewebsites.net`) | No |
| `STATIC_WEB_APP_TOKEN` | Azure Portal → Static Web App → Manage deployment token | **Yes** |

### How to get each value

**After running `deploy.ps1`**, all values are printed to the terminal. Copy them.

For `STATIC_WEB_APP_TOKEN`:
1. Azure Portal → search **Static Web Apps** → select `hods-ui-...`
2. Click **Manage deployment token** → copy the token
3. Paste as a **secret** pipeline variable

---

## Step 5 — Create the `production` Environment with approval gate

The deploy stages use an environment named `production`. Add a manual approval so no one can deploy without sign-off:

1. **Pipelines** → **Environments** → **New environment**
   - Name: `production`
   - Resource: **None**
2. Click the `...` menu on the environment → **Approvals and checks** → **Approvals**
3. Add yourself (or your team) as approver → **Create**

Now every deployment to production waits for a human to approve it.

---

## Step 6 — Run the infrastructure pipeline first

1. Go to the `hods-infra` pipeline → **Run pipeline** → **Run**
2. It will validate (Bicep lint + what-if) and then pause for approval before deploying
3. After approval, it deploys all Azure resources and sets up the search index

Then run `hods-ingest`, `hods-api`, and `hods-ui` in any order.

---

## Pipeline overview

```
PR opened
  └─ Validate/CI stage runs automatically (lint + tests)
     └─ Pass → reviewer can approve PR → merge to main
        └─ Deploy stage triggers → waits for manual approval in 'production' environment
           └─ Approved → deploys to Azure
```

---

## Branch policy (recommended)

Protect `main` so only passing pipelines can merge:

1. **Repos** → **Branches** → `main` → **...** → **Branch policies**
2. Enable:
   - **Require a minimum number of reviewers**: 1
   - **Check for linked work items**: optional
   - **Build validation**: add each of the four pipelines
   - **Require all comments to be resolved**: Yes
