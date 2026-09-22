// Azure AI Services account (kind AIServices) exposing Azure OpenAI and Content Safety
// behind one endpoint, plus the two model deployments the assistant needs (ADR-007).
// Local keys are disabled: callers authenticate with Entra ID only (objective OT2).

@description('Account name, also used as the custom subdomain, so it must be globally unique.')
@minLength(2)
@maxLength(64)
param name string

param location string
param tags object

@description('Object id of the managed identity used by the API.')
param appPrincipalId string

@description('Object id of the human operator running scripts locally. Empty means no assignment.')
param developerPrincipalId string = ''

@description('Chat model deployment. Capacity is expressed in thousands of tokens per minute.')
param chatDeployment object = {
  name: 'gpt-5.4-mini'
  modelName: 'gpt-5.4-mini'
  modelVersion: '2026-03-17'
  skuName: 'DataZoneStandard'
  capacity: 30
}

@description('Embedding model deployment.')
param embeddingDeployment object = {
  name: 'text-embedding-3-small'
  modelName: 'text-embedding-3-small'
  modelVersion: '1'
  skuName: 'DataZoneStandard'
  capacity: 50
}

import { roleIds } from '../shared/roles.bicep'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    // S0 is the only pay-as-you-go tier. Nothing is billed until a model is called.
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // A custom subdomain is required for token authentication and for Entra ID based access.
    customSubDomainName: name
    // No API keys at all: listKeys stops working and only Entra tokens are accepted.
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Model deployments must be created one at a time: the service rejects parallel writes on the
// same account. batchSize(1) turns the loop into a sequence.
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = [
  for deployment in [ chatDeployment, embeddingDeployment ]: {
    parent: account
    name: deployment.name
    sku: {
      name: deployment.skuName
      // Capacity is the slice of the regional quota this deployment reserves.
      capacity: deployment.capacity
    }
    properties: {
      model: {
        format: 'OpenAI'
        name: deployment.modelName
        version: deployment.modelVersion
      }
      // Keep the version fixed: an automatic upgrade would change model behaviour without a
      // deployment, and every evaluation result would silently refer to another model.
      versionUpgradeOption: 'NoAutoUpgrade'
      raiPolicyName: 'Microsoft.DefaultV2'
    }
  }
]

// Calling chat completions and embeddings.
resource appOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, appPrincipalId, roleIds.cognitiveServicesOpenAiUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.cognitiveServicesOpenAiUser)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Calling the other AI Services APIs on this account, Content Safety and Prompt Shields included.
resource appCognitiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, appPrincipalId, roleIds.cognitiveServicesUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.cognitiveServicesUser)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource devOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(account.id, developerPrincipalId, roleIds.cognitiveServicesOpenAiUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.cognitiveServicesOpenAiUser)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

resource devCognitiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(account.id, developerPrincipalId, roleIds.cognitiveServicesUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.cognitiveServicesUser)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output id string = account.id
output name string = account.name

@description('Base endpoint, for example https://aif-coffeeai-dev-frc.cognitiveservices.azure.com/')
output endpoint string = account.properties.endpoint

@description('Deployment name the application passes as the model argument.')
output chatDeploymentName string = chatDeployment.name

output embeddingDeploymentName string = embeddingDeployment.name
