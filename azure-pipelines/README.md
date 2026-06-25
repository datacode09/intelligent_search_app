# Azure DevOps — Pipeline Setup Guide

One pipeline, for the ingest Function App. It runs lint + tests on every
PR and deploys on merge to `main`.

---

## Step 1 — Import this repo into Azure DevOps

1. Go to https://dev.azure.com and sign in (create a free account if needed)
2. Create a new **Organization** and a **Project** (e.g. `hods-ingest`)
3. Go to **Repos** → **Import a repository**
   - Source: your existing remote, or push directly
4. After import, your code lives in Azure Repos

---

## Step 2 — Create an Azure Service Connection

This lets the pipeline deploy to your Azure subscription without storing
passwords.

1. **Project Settings** → **Service connections** → **New service connection**
2. Select **Azure Resource Manager** → **Workload Identity Federation (automatic)**
3. Scope: **Subscription** → select your subscription → Resource group: the
   one holding the Function App
4. Name it exactly: **`hods-azure-service-connection`** (matches the
   `serviceConnection` variable in `ingest.yml`)
5. Check **Grant access permission to all pipelines** → **Save**

---

## Step 3 — Create the pipeline

1. **Pipelines** → **New pipeline**
2. Where is your code? → **Azure Repos Git** → select your repo
3. Configure: **Existing Azure Pipelines YAML file**
4. Select `azure-pipelines/ingest.yml` → **Continue** → **Save** (don't run yet)
5. Name the pipeline `hods-ingest`

---

## Step 4 — Set pipeline variables

Set this on the pipeline under **Edit → Variables**:

| Variable | Value | Secret? |
|---|---|---|
| `FUNCTION_APP_NAME` | The name of the Function App resource in Azure (e.g. `hods-ingest-xxxxxxxx`) | No |

---

## Step 5 — Create the `production` Environment with approval gate

The deploy stage uses an environment named `production`. Add a manual
approval so no one can deploy without sign-off:

1. **Pipelines** → **Environments** → **New environment**
   - Name: `production`
   - Resource: **None**
2. Click the `...` menu on the environment → **Approvals and checks** → **Approvals**
3. Add yourself (or your team) as approver → **Create**

Now every deployment to production waits for a human to approve it.

---

## Step 6 — Run the pipeline

The Function App resource itself needs to already exist (deployed via
`infra/main.bicep`, separately from this pipeline — see
[`docs/DEV-GUIDE-INFRA.md`](../docs/DEV-GUIDE-INFRA.md)). Once it exists:

1. Go to the `hods-ingest` pipeline → **Run pipeline** → **Run**
2. It validates (lint + tests), then pauses for approval before deploying
3. After approval, it deploys the function code

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

Protect `main` so only a passing pipeline can merge:

1. **Repos** → **Branches** → `main` → **...** → **Branch policies**
2. Enable:
   - **Require a minimum number of reviewers**: 1
   - **Check for linked work items**: optional
   - **Build validation**: add the `hods-ingest` pipeline
   - **Require all comments to be resolved**: Yes
