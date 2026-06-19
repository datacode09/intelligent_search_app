// HODS Intelligent Search App — Azure Infrastructure
// Deploys: Storage, AI Search, Azure OpenAI, Key Vault, App Insights,
//          Function App (ingest), App Service (API), Static Web App (UI)

@description('Short prefix used to name all resources (3-6 lowercase letters)')
param prefix string = 'hods'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Your name or org — used in tags')
param owner string = 'your-name'

@description('Object ID of the deploying user/SP — granted Key Vault access')
param deployerObjectId string

@description('Allowed CORS origins for the API (comma-separated)')
param allowedOrigins string = 'http://localhost:5173'

var tags = {
  project: 'hods-intelligent-search'
  owner: owner
}
var unique = uniqueString(resourceGroup().id)

// ─── Storage Account ────────────────────────────────────────────────────────
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: '${prefix}st${unique}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource ingestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'ingest-output'
  properties: { publicAccess: 'None' }
}

// ─── Key Vault ───────────────────────────────────────────────────────────────
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv-${unique}'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
  }
}

// Deployer gets Key Vault Secrets Officer so they can write secrets
resource kvDeployerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerObjectId, 'kv-officer')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
    principalId: deployerObjectId
    principalType: 'User'
  }
}

// Store storage connection string in Key Vault
resource kvSecretStorage 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'blob-storage-connection-string'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
  }
}

// ─── Log Analytics + Application Insights ───────────────────────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${unique}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-insights-${unique}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    RetentionInDays: 90
  }
}

// ─── Azure AI Search ────────────────────────────────────────────────────────
resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${prefix}-search-${unique}'
  location: location
  tags: tags
  sku: { name: 'basic' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
  }
}

// ─── Azure OpenAI ────────────────────────────────────────────────────────────
resource openAI 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: '${prefix}-oai-${unique}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: '${prefix}-oai-${unique}'
  }
}

resource gptDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAI
  name: 'gpt-4o'
  sku: { name: 'Standard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4o', version: '2024-11-20' }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAI
  name: 'text-embedding-3-large'
  sku: { name: 'Standard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'text-embedding-3-large', version: '1' }
  }
  dependsOn: [gptDeployment]
}

// ─── App Service Plan ────────────────────────────────────────────────────────
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${prefix}-plan-${unique}'
  location: location
  tags: tags
  sku: { name: 'P1v3', tier: 'PremiumV3' }  // Production-grade: autoscale capable
  kind: 'linux'
  properties: { reserved: true }
}

// Autoscale: 1–3 instances based on CPU
resource autoscale 'Microsoft.Insights/autoscalesettings@2022-10-01' = {
  name: '${prefix}-autoscale-${unique}'
  location: location
  tags: tags
  properties: {
    enabled: true
    targetResourceUri: appServicePlan.id
    profiles: [
      {
        name: 'default'
        capacity: { default: '1', minimum: '1', maximum: '3' }
        rules: [
          {
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: appServicePlan.id
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT5M'
              timeAggregation: 'Average'
              operator: 'GreaterThan'
              threshold: 70
            }
            scaleAction: { direction: 'Increase', type: 'ChangeCount', value: '1', cooldown: 'PT5M' }
          }
          {
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: appServicePlan.id
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT10M'
              timeAggregation: 'Average'
              operator: 'LessThan'
              threshold: 30
            }
            scaleAction: { direction: 'Decrease', type: 'ChangeCount', value: '1', cooldown: 'PT10M' }
          }
        ]
      }
    ]
  }
}

// ─── Function App (poc-hods-ingest) ─────────────────────────────────────────
resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: '${prefix}-ingest-${unique}'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.13'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage', value: '@Microsoft.KeyVault(SecretUri=${kvSecretStorage.properties.secretUri})' }
        { name: 'BLOB_STORAGE_CONNECTION_STRING', value: '@Microsoft.KeyVault(SecretUri=${kvSecretStorage.properties.secretUri})' }
        { name: 'BLOB_CONTAINER_NAME', value: 'ingest-output' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        // Fill these in the Azure Portal after Step 3 of SETUP.md:
        { name: 'SHAREPOINT_TENANT_ID', value: 'REPLACE_ME' }
        { name: 'SHAREPOINT_CLIENT_ID', value: 'REPLACE_ME' }
        { name: 'SHAREPOINT_CLIENT_SECRET', value: 'REPLACE_ME' }
        { name: 'SHAREPOINT_SITE_HOSTNAME', value: 'REPLACE_ME.sharepoint.com' }
        { name: 'SHAREPOINT_SITE_PATH', value: '/sites/REPLACE_ME' }
        { name: 'SHAREPOINT_LIBRARY_DRIVE_NAME', value: 'Documents' }
      ]
    }
  }
}

// Function App → Key Vault Secrets User
resource fnKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, 'kv-secrets-user')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ─── API App Service (hydro-one-hods-api) ────────────────────────────────────
resource apiApp 'Microsoft.Web/sites@2023-01-01' = {
  name: '${prefix}-api-${unique}'
  location: location
  tags: tags
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      minTlsVersion: '1.2'
      appCommandLine: 'pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000'
      appSettings: [
        { name: 'AZURE_OPENAI_BASE_URL', value: '${openAI.properties.endpoint}openai/v1/' }
        { name: 'AZURE_OPENAI_API_VERSION', value: 'preview' }
        { name: 'AZURE_OPENAI_DEPLOYMENT_NAME', value: 'gpt-4o' }
        { name: 'AZURE_OPENAI_TOKEN_SCOPE', value: 'https://cognitiveservices.azure.com/.default' }
        { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${searchService.name}.search.windows.net' }
        { name: 'AZURE_SEARCH_INDEX_NAME', value: 'hods-index' }
        { name: 'AZURE_SEARCH_SEMANTIC_CONFIGURATION', value: 'hods-index-semantic-configuration' }
        { name: 'AZURE_SEARCH_TOKEN_SCOPE', value: 'https://search.azure.com/.default' }
        { name: 'AZURE_TENANT_ID', value: subscription().tenantId }
        { name: 'AZURE_CLIENT_ID', value: 'REPLACE_WITH_API_APP_REGISTRATION_CLIENT_ID' }
        { name: 'ALLOWED_ORIGINS', value: allowedOrigins }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
      ]
    }
  }
}

// API → OpenAI (Cognitive Services OpenAI User)
resource apiOpenAIRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAI.id, apiApp.id, 'openai-user')
  scope: openAI
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// API → Search (Search Index Data Reader)
resource apiSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, apiApp.id, 'search-reader')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Static Web App (hydro-one-hods-ui) ──────────────────────────────────────
resource staticWebApp 'Microsoft.Web/staticSites@2023-01-01' = {
  name: '${prefix}-ui-${unique}'
  location: 'eastus2'  // Static Web Apps have limited region support
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {}
}

// ─── Outputs ─────────────────────────────────────────────────────────────────
output storageAccountName string = storageAccount.name
output keyVaultName string = keyVault.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
output searchServiceName string = searchService.name
output openAIEndpoint string = openAI.properties.endpoint
output openAIName string = openAI.name
output functionAppName string = functionApp.name
output apiAppName string = apiApp.name
output apiAppUrl string = 'https://${apiApp.properties.defaultHostName}'
output staticWebAppUrl string = 'https://${staticWebApp.properties.defaultHostname}'
output staticWebAppName string = staticWebApp.name
