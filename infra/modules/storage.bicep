// Storage account holding product images and the recommender model artefacts.
// Shared keys are disabled on purpose: every caller authenticates with Entra ID (objective OT2).

@description('Storage account name: lower case letters and digits only, 3 to 24 characters.')
@minLength(3)
@maxLength(24)
param name string

param location string
param tags object

@description('Object id of the managed identity that reads blobs at runtime.')
param appPrincipalId string

@description('Object id of the human operator who runs the ingestion pipelines. Empty means no assignment.')
param developerPrincipalId string = ''

var containers = [
  'product-images'
  'model-artefacts'
]

// Role ids live in one shared file, next to the command that produced them.
import { roleIds } from '../shared/roles.bicep'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    // Locally redundant storage: three copies inside one datacentre. Enough for a dev workload.
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // No account key, no connection string: tokens only. This is what makes NFR6 provable.
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for containerName in containers: {
    parent: blobService
    name: containerName
    properties: {
      publicAccess: 'None'
    }
  }
]

// The API only ever reads blobs, so it gets the reader role and nothing more.
resource appBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, appPrincipalId, roleIds.storageBlobDataReader)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageBlobDataReader)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// The operator uploads images and model artefacts, so they need write access on the data plane.
resource devBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(storage.id, developerPrincipalId, roleIds.storageBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageBlobDataContributor)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output id string = storage.id
output name string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
