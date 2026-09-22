// Azure Container Registry holding the API image.
// The admin account stays disabled: Container Apps pulls with its managed identity.

@description('Registry name: alphanumeric only, 5 to 50 characters.')
@minLength(5)
@maxLength(50)
param name string

param location string
param tags object

@description('Object id of the managed identity that pulls the image.')
param appPrincipalId string

// AcrPull allows pulling images and nothing else.
import { roleIds } from '../shared/roles.bicep'

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    // Basic: 10 GB of storage, enough for a single application image.
    name: 'Basic'
  }
  properties: {
    // A shared admin password would be a credential to store and rotate. Managed identity instead.
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource appAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, appPrincipalId, roleIds.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPull)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output id string = registry.id
output name string = registry.name
output loginServer string = registry.properties.loginServer
