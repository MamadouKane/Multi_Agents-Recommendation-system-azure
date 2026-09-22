// Key Vault for the few secrets that cannot be replaced by a managed identity,
// for example a third-party webhook token. Access is granted by RBAC, not by access policies.

@description('Key Vault name: 3 to 24 characters, globally unique.')
@minLength(3)
@maxLength(24)
param name string

param location string
param tags object

@description('Object id of the managed identity that reads secrets at runtime.')
param appPrincipalId string

@description('Object id of the human operator who writes secrets. Empty means no assignment.')
param developerPrincipalId string = ''

@description('Soft delete retention in days. 7 is the minimum and keeps dev cleanup easy.')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 7

import { roleIds } from '../shared/roles.bicep'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    // RBAC instead of the legacy access policy model: same permission system as every other service.
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    // Purge protection would block a real delete for the whole retention window. Off in dev.
    enablePurgeProtection: null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Read secret values, nothing else. Cannot list, create or delete secrets.
resource appSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, appPrincipalId, roleIds.keyVaultSecretsUser)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource devSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(vault.id, developerPrincipalId, roleIds.keyVaultSecretsOfficer)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsOfficer)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output id string = vault.id
output name string = vault.name
output uri string = vault.properties.vaultUri
