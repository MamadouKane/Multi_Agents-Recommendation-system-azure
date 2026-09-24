// Azure AI Search: hybrid retrieval with the semantic ranker (ADR-002).
// This is the only resource in the workload billed by the hour, so it is created and deleted
// per working session with `make search-up` and `make search-down`.

@description('Search service name: lower case, globally unique, 2 to 60 characters.')
@minLength(2)
@maxLength(60)
param name string

param location string
param tags object

@description('Object id of the managed identity that queries the index at runtime.')
param appPrincipalId string

@description('Object id of the human operator who builds the index. Empty means no assignment.')
param developerPrincipalId string = ''

@description('Basic carries the semantic ranker. Free does not, see ADR-002.')
@allowed([ 'free', 'basic', 'standard' ])
param sku string = 'basic'

import { roleIds } from '../shared/roles.bicep'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    // One search unit: 1 replica times 1 partition, the smallest billable shape.
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    // Semantic ranker on the free plan: a monthly allowance of queries at no cost, which covers
    // development and the day 2 ablation. Switching to 'standard' is a one word change.
    semanticSearch: 'free'
    // Admin and query keys disabled: Entra ID only, like every other service here.
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
  }
}

// Querying an index at runtime. Cannot create or delete an index.
resource appIndexReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, appPrincipalId, roleIds.searchIndexDataReader)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.searchIndexDataReader)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Writing documents into an index, for the day 2 build pipeline.
resource devIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(search.id, developerPrincipalId, roleIds.searchIndexDataContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.searchIndexDataContributor)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

// Creating the index definition itself, which is a control plane operation.
resource devServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(search.id, developerPrincipalId, roleIds.searchServiceContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.searchServiceContributor)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
