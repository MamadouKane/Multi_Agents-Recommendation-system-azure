// Entry point for the coffee shop assistant infrastructure.
// Scope is the resource group: the group itself is created outside this template.
// Naming and tagging follow docs/adr/006-naming-and-environments.md.
targetScope = 'resourceGroup'

@description('Workload name used in every resource name.')
@minLength(3)
@maxLength(12)
param workload string = 'coffeeai'

@description('Environment short name.')
@allowed([ 'dev', 'stg' ])
param env string = 'dev'

@description('Azure region. Defaults to the region of the resource group.')
param location string = resourceGroup().location

@description('Short region code used in resource names, for example frc for France Central.')
@maxLength(4)
param regionCode string = 'frc'

@description('Owner tag, used to tell resources apart in Cost Management.')
param owner string = 'mamadou-kane'

@description('Log Analytics retention in days. 30 days is the free tier allowance.')
@minValue(30)
@maxValue(730)
param logRetentionInDays int = 30

@description('Daily ingestion cap in GB. Protects the budget if something loops and floods the logs.')
param logDailyQuotaGb int = 1

@description('Object id of the human operator, from: az ad signed-in-user show --query id -o tsv. Grants data plane roles for local runs.')
param developerPrincipalId string = ''

@description('Deploy Azure AI Search. It is the only hourly billed resource, so it is created per working session (ADR-002).')
param deploySearch bool = true

// Tags are applied to every resource. managed-by makes hand-created resources easy to spot.
var tags = {
  workload: workload
  env: env
  owner: owner
  'managed-by': 'bicep'
  'cost-center': 'portfolio'
}

// <type>-<workload>-<env>-<region> for resources that accept hyphens.
var suffix = '${workload}-${env}-${regionCode}'

// Storage accounts and registries accept neither hyphens nor upper case.
var compactSuffix = '${workload}${env}${regionCode}'

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    workspaceName: 'log-${suffix}'
    appInsightsName: 'appi-${suffix}'
    location: location
    tags: tags
    retentionInDays: logRetentionInDays
    dailyQuotaGb: logDailyQuotaGb
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: 'id-${suffix}'
    location: location
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    name: 'st${compactSuffix}'
    location: location
    tags: tags
    appPrincipalId: identity.outputs.principalId
    developerPrincipalId: developerPrincipalId
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    name: 'cr${compactSuffix}'
    location: location
    tags: tags
    appPrincipalId: identity.outputs.principalId
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: 'kv-${suffix}'
    location: location
    tags: tags
    appPrincipalId: identity.outputs.principalId
    developerPrincipalId: developerPrincipalId
  }
}

module aiServices 'modules/ai-services.bicep' = {
  name: 'ai-services'
  params: {
    name: 'aif-${suffix}'
    location: location
    tags: tags
    appPrincipalId: identity.outputs.principalId
    developerPrincipalId: developerPrincipalId
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    name: 'cosmos-${suffix}'
    location: location
    tags: tags
    appPrincipalId: identity.outputs.principalId
    developerPrincipalId: developerPrincipalId
  }
}

module search 'modules/search.bicep' = if (deploySearch) {
  name: 'search'
  params: {
    name: 'srch-${suffix}'
    location: location
    tags: tags
    appPrincipalId: identity.outputs.principalId
    developerPrincipalId: developerPrincipalId
  }
}

module containerApp 'modules/containerapp.bicep' = {
  name: 'container-app'
  params: {
    environmentName: 'cae-${suffix}'
    appName: 'ca-${workload}-api-${env}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    appInsightsName: monitoring.outputs.appInsightsName
    managedIdentityId: identity.outputs.id
    managedIdentityClientId: identity.outputs.clientId
    serviceEndpoints: {
      openAi: aiServices.outputs.endpoint
      chatDeployment: aiServices.outputs.chatDeploymentName
      embeddingDeployment: aiServices.outputs.embeddingDeploymentName
      cosmos: cosmos.outputs.endpoint
      cosmosDatabase: cosmos.outputs.databaseName
      search: deploySearch ? search!.outputs.endpoint : ''
      storageBlob: storage.outputs.blobEndpoint
    }
  }
}

@description('Resource id of the Log Analytics workspace, consumed by diagnostic settings later.')
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId

@description('Name of the Application Insights component.')
output appInsightsName string = monitoring.outputs.appInsightsName

@description('Resource id of the user-assigned managed identity, attached to the Container App later.')
output managedIdentityId string = identity.outputs.id

@description('Object id used in every RBAC role assignment.')
output managedIdentityPrincipalId string = identity.outputs.principalId

@description('Client id the application passes to DefaultAzureCredential.')
output managedIdentityClientId string = identity.outputs.clientId

@description('Blob endpoint, used by the ingestion pipeline and by the API.')
output storageBlobEndpoint string = storage.outputs.blobEndpoint

output storageAccountName string = storage.outputs.name

@description('Registry login server, used as the image prefix in the deployment workflow.')
output containerRegistryLoginServer string = acr.outputs.loginServer

output keyVaultUri string = keyVault.outputs.uri

@description('AI Services endpoint: Azure OpenAI and Content Safety share it.')
output aiServicesEndpoint string = aiServices.outputs.endpoint

output chatDeploymentName string = aiServices.outputs.chatDeploymentName

output embeddingDeploymentName string = aiServices.outputs.embeddingDeploymentName

@description('Cosmos DB endpoint read by the catalogue loader and the conversation writer.')
output cosmosEndpoint string = cosmos.outputs.endpoint

output cosmosDatabaseName string = cosmos.outputs.databaseName

@description('Search endpoint, empty when the service is torn down between sessions.')
output searchEndpoint string = deploySearch ? search!.outputs.endpoint : ''

@description('Public URL of the API. Day 1 serves the placeholder image, day 6 serves the real one.')
output apiUrl string = 'https://${containerApp.outputs.fqdn}'

output containerAppName string = containerApp.outputs.appName
