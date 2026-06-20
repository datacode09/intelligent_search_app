# Development & Testing Guide — Infrastructure Component (`infra/`)

## Overview

All Azure resources are defined in a single Bicep file (`infra/main.bicep`). A PowerShell script (`scripts/deploy.ps1`) creates the resource group and triggers the deployment. A Python script (`scripts/create-indexer.py`) provisions the AI Search index, data source, skillset, and indexer after the ARM deployment completes.

**Technology:** Azure Bicep, Azure CLI, PowerShell, Python 3.12

---

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) 2.60+
- PowerShell 7+ (or Windows PowerShell 5.1)
- Python 3.12 (for `scripts/create-indexer.py`)
- An Azure subscription with Contributor access
- Object ID of the deploying user or service principal (for Key Vault RBAC)

```bash
# Get your own object ID
az ad signed-in-user show --query id -o tsv
```

---

## Resource Summary

The Bicep template deploys the following resources:

| Resource | SKU/Tier | Notes |
|---|---|---|
| Storage Account | Standard LRS | Blob container `ingest-output` |
| Key Vault | Standard | RBAC auth; soft delete 90d; purge protection |
| Log Analytics Workspace | PerGB2018 | 90-day retention |
| Application Insights | Web | Linked to Log Analytics |
| Azure AI Search | Basic (→ upgrade to Standard S1 for prod) | Semantic search enabled |
| Azure OpenAI | S0 | `gpt-4o` + `text-embedding-3-large` deployments |
| App Service Plan | P1v3 PremiumV3 Linux | Shared by Function App and API |
| Autoscale setting | 1–3 instances | CPU >70% scale-out, <30% scale-in |
| Function App | Python 3.13 | Timer trigger; Key Vault secret references |
| API App Service | Python 3.12 | System Assigned Managed Identity |
| Static Web App | Standard | UI hosting |

---

## Deploying to a Development Environment

### 1. Log in

```bash
az login
az account set --subscription "<subscription-id>"
```

### 2. Run the deployment script

```powershell
# Windows
.\scripts\deploy.ps1 `
  -ResourceGroupName "hods-dev-rg" `
  -Location "canadacentral" `
  -Prefix "hods" `
  -DeployerObjectId "<your-object-id>"
```

The script will:
1. Create the resource group if it doesn't exist
2. Deploy `infra/main.bicep` and wait for completion
3. Write output values (endpoints, names) to a local `.env` file

### 3. Provision the AI Search pipeline

After Bicep deployment completes, run the indexer setup script:

```bash
pip install requests azure-identity
python scripts/create-indexer.py \
  --search-endpoint "https://<search-name>.search.windows.net" \
  --storage-connection-string "<blob-connection-string>" \
  --openai-endpoint "https://<openai-name>.openai.azure.com"
```

This creates: Data Source → Skillset → Index → Indexer (in order).

### 4. Verify the deployment

```bash
# Check Function App is running
az functionapp show --name <function-app-name> --resource-group hods-dev-rg \
  --query "state" -o tsv
# Expected: Running

# Check API App Service is running
az webapp show --name <api-app-name> --resource-group hods-dev-rg \
  --query "state" -o tsv
# Expected: Running

# Verify Search index was created
az search index list --service-name <search-name> --resource-group hods-dev-rg \
  --query "[].name" -o tsv
```

---

## Validating Bicep Before Deployment

Always validate before deploying to avoid partial rollouts:

```bash
# Validate template syntax and parameter types
az deployment group validate \
  --resource-group hods-dev-rg \
  --template-file infra/main.bicep \
  --parameters prefix=hods deployerObjectId=<your-object-id>

# What-if: preview resource changes without deploying
az deployment group what-if \
  --resource-group hods-dev-rg \
  --template-file infra/main.bicep \
  --parameters prefix=hods deployerObjectId=<your-object-id>
```

Review the what-if output carefully before applying to any shared or production environment.

---

## RBAC Role Assignments

The Bicep template creates four role assignments automatically:

| Principal | Role | Scope | Role ID |
|---|---|---|---|
| Deploying user | Key Vault Secrets Officer | Key Vault | `b86a8fe4-...` |
| Function App (MI) | Key Vault Secrets User | Key Vault | `4633458b-...` |
| API App (MI) | Cognitive Services OpenAI User | Azure OpenAI | `5e0bd9bd-...` |
| API App (MI) | Search Index Data Reader | AI Search | `1407120a-...` |

**ISSUE-7 (not yet done):** The Function App also needs `Storage Blob Data Contributor` on the Storage Account when connection string auth is replaced with Managed Identity. Add to `main.bicep`:

```bicep
resource fnStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, 'blob-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

---

## AI Search Index Notes

### Index fields (`infra/search-index.json`)

| Field | Type | Notes |
|---|---|---|
| `chunk_id` | `Edm.String` (key) | Unique per chunk |
| `parent_id` | `Edm.String` | Shared across all chunks of a document |
| `title` | `Edm.String` | Blob filename |
| `content` | `Edm.String` | Chunk text |
| `text_vector` | `Collection(Edm.Single)` | 3072-dim embedding (text-embedding-3-large) |
| `Prefix` | `Edm.String` | Filterable/facetable |
| `ContentType` | `Collection(Edm.String)` | Filterable/facetable |
| `SourceUrl` | `Edm.String` | **Not yet in index** — add as part of ISSUE-4 fix |

### Production SKU upgrade (ISSUE-10)

The current `basic` SKU has no SLA. For production change in `main.bicep`:

```bicep
sku: { name: 'standard' }
properties: {
  replicaCount: 2       // 99.9% SLA requires >= 2 replicas
  partitionCount: 1
  hostingMode: 'default'
  semanticSearch: 'standard'
}
```

---

## Teardown

To delete all resources and avoid ongoing charges:

```bash
az group delete --name hods-dev-rg --yes --no-wait
```

> Key Vault has purge protection enabled (90-day retention). The name will be reserved until the soft-delete period expires. Use a different prefix for redeployment.
