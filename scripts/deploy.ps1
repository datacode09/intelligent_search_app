# HODS Stack — One-shot Azure deployment script
# Run this from the intelligent_search_app root directory in PowerShell.
# Prerequisites: Azure CLI installed and logged in (az login)

param(
    [string]$ResourceGroup = "hods-rg",
    [string]$Location      = "eastus",
    [string]$Prefix        = "hods"
)

$ErrorActionPreference = "Stop"

Write-Host "`n=== HODS Deployment ===" -ForegroundColor Cyan

# 1. Create resource group
Write-Host "`n[1/4] Creating resource group '$ResourceGroup' in '$Location'..."
az group create --name $ResourceGroup --location $Location | Out-Null
Write-Host "  Done." -ForegroundColor Green

# 2. Deploy Bicep
Write-Host "`n[2/4] Deploying Azure resources (this takes ~5 min)..."
$output = az deployment group create `
    --resource-group $ResourceGroup `
    --template-file infra/main.bicep `
    --parameters prefix=$Prefix owner=$env:USERNAME `
    --query properties.outputs `
    --output json | ConvertFrom-Json

Write-Host "  Done." -ForegroundColor Green

# 3. Print outputs
$searchEndpoint   = $output.searchEndpoint.value
$openAIEndpoint   = $output.openAIEndpoint.value
$storageConn      = $output.storageConnectionString.value
$functionAppName  = $output.functionAppName.value
$apiAppName       = $output.apiAppName.value
$apiAppUrl        = $output.apiAppUrl.value

Write-Host "`n=== Deployment outputs ===" -ForegroundColor Yellow
Write-Host "Storage connection : $storageConn"
Write-Host "Search endpoint    : $searchEndpoint"
Write-Host "OpenAI endpoint    : $openAIEndpoint"
Write-Host "Function App       : $functionAppName"
Write-Host "API App            : $apiAppUrl"

# 4. Create Search index + indexer
Write-Host "`n[3/4] Setting up Azure AI Search index and indexer..."
$env:AZURE_SEARCH_ENDPOINT        = $searchEndpoint
$env:AZURE_OPENAI_BASE_URL        = "${openAIEndpoint}openai/v1/"
$env:BLOB_STORAGE_CONNECTION_STRING = $storageConn

python scripts/create-indexer.py
Write-Host "  Done." -ForegroundColor Green

# 5. Write local .env files
Write-Host "`n[4/4] Writing local config files..."

@"
AZURE_OPENAI_BASE_URL=${openAIEndpoint}openai/v1/
AZURE_OPENAI_API_VERSION=preview
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_TOKEN_SCOPE=https://cognitiveservices.azure.com/.default

AZURE_SEARCH_ENDPOINT=$searchEndpoint
AZURE_SEARCH_INDEX_NAME=hods-index
AZURE_SEARCH_SEMANTIC_CONFIGURATION=hods-index-semantic-configuration
AZURE_SEARCH_TOKEN_SCOPE=https://search.azure.com/.default
"@ | Out-File -FilePath "hydro-one-hods-api/.env" -Encoding utf8

Write-Host "  Wrote hydro-one-hods-api/.env" -ForegroundColor Green

@"
VITE_API_TARGET=http://localhost:8000
VITE_API_BASE_URL=
"@ | Out-File -FilePath "hydro-one-hods-ui/.env.local" -Encoding utf8

Write-Host "  Wrote hydro-one-hods-ui/.env.local" -ForegroundColor Green

Write-Host "`n=== All done! ===" -ForegroundColor Cyan
Write-Host "Next step: fill in SharePoint credentials in the Function App settings."
Write-Host "See SETUP.md Step 3 for instructions."
