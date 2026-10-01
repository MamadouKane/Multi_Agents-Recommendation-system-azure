// Azure Machine Learning: the workspace and a training cluster that scales to zero (day 4).
//
// The workspace reuses the project's storage, vault, registry and Application Insights instead of
// creating its own. Storage refuses shared keys, so the workspace datastores authenticate with
// identities (systemDatastoresAuthMode). Training runs as its own managed identity, allowed to
// write to storage, while the API identity stays read only.
//
// A compute cluster, not a compute instance: it starts for a job and goes back to zero nodes two
// minutes after, so "stop the compute at the end of the day" cannot be forgotten.

@description('Workspace name.')
param name string

@description('Training cluster name, 2 to 16 characters.')
@maxLength(16)
param clusterName string = 'cpu-cluster'

@description('Training identity name.')
param trainingIdentityName string

param location string
param tags object

param storageAccountId string
param keyVaultId string
param appInsightsId string
param containerRegistryId string

@description('Object id of the developer who submits jobs. Empty means no assignment.')
param developerPrincipalId string = ''

@description('VM size of the training nodes. DSv2 has 6 vCPUs of Azure ML quota in France Central.')
param vmSize string = 'Standard_DS3_v2'

import { roleIds } from '../shared/roles.bicep'

resource trainingIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: trainingIdentityName
  location: location
  tags: tags
}

resource workspace 'Microsoft.MachineLearningServices/workspaces@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'Default'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  properties: {
    friendlyName: 'Coffee shop recommender'
    storageAccount: storageAccountId
    keyVault: keyVaultId
    applicationInsights: appInsightsId
    containerRegistry: containerRegistryId
    // No account keys anywhere: datastores use the caller's or the compute's identity.
    systemDatastoresAuthMode: 'identity'
    publicNetworkAccess: 'Enabled'
    v1LegacyMode: false
  }
}

resource cluster 'Microsoft.MachineLearningServices/workspaces/computes@2025-06-01' = {
  parent: workspace
  name: clusterName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${trainingIdentity.id}': {}
    }
  }
  properties: {
    computeType: 'AmlCompute'
    properties: {
      vmSize: vmSize
      vmPriority: 'Dedicated'
      osType: 'Linux'
      scaleSettings: {
        minNodeCount: 0
        maxNodeCount: 1
        nodeIdleTimeBeforeScaleDown: 'PT2M'
      }
      remoteLoginPortPublicAccess: 'Disabled'
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(storageAccountId, '/'))
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(split(containerRegistryId, '/'))
}

// The workspace identity. Azure ML grants it, by itself and on creation, Storage Blob Data
// Contributor and Storage File Data Privileged Contributor on the storage account, Key Vault
// Administrator on the vault and Azure AI Administrator on the resource group. Declaring those here
// fails with RoleAssignmentExists, so only what the service does not grant is declared: pushing
// environment images to the project's registry.
resource workspaceRegistry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, workspace.id, roleIds.acrPush)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPush)
    principalId: workspace.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// The training identity: reads the data asset and writes the step outputs.
resource trainingBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, trainingIdentity.id, roleIds.storageBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageBlobDataContributor)
    principalId: trainingIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource trainingRegistry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, trainingIdentity.id, roleIds.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPull)
    principalId: trainingIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// The training identity also logs MLflow runs and registers the model from inside a job.
resource trainingWorkspace 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, trainingIdentity.id, roleIds.azureMlDataScientist)
  scope: workspace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.azureMlDataScientist)
    principalId: trainingIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource developerWorkspace 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(workspace.id, developerPrincipalId, roleIds.azureMlDataScientist)
  scope: workspace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.azureMlDataScientist)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output workspaceName string = workspace.name
output clusterName string = cluster.name
output trainingIdentityClientId string = trainingIdentity.properties.clientId
