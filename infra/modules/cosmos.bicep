// Cosmos DB for NoSQL, serverless: the product catalogue and the conversation history.
// Cosmos has its own data plane RBAC system, separate from Azure RBAC, handled at the bottom.

@description('Account name: lower case, globally unique.')
@minLength(3)
@maxLength(44)
param name string

param location string
param tags object

@description('Database name.')
param databaseName string = 'coffeeshop'

@description('Object id of the managed identity used by the API.')
param appPrincipalId string

@description('Object id of the human operator running ingestion locally. Empty means no assignment.')
param developerPrincipalId string = ''

@description('Conversation retention in seconds. 90 days, per NFR11.')
param conversationsTtlSeconds int = 7776000

// Cosmos ships two built-in data plane roles, with fixed ids scoped to the account:
// ...0001 is Data Reader, ...0002 is Data Contributor (read and write documents).
var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    // Serverless: billed per request unit consumed, with no minimum. At this volume it is
    // a couple of euros per month, and nothing at all while the app is idle.
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    // Session consistency: a client always reads its own writes, which is what a chat turn needs.
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    // Same rule as everywhere else: account keys are off, Entra ID only.
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    minimalTlsVersion: 'Tls12'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// Partition key /category: queries filter by category, and the catalogue is small enough
// that no partition gets hot.
resource productsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'products'
  properties: {
    resource: {
      id: 'products'
      partitionKey: {
        paths: [ '/category' ]
        kind: 'Hash'
      }
    }
  }
}

// Partition key /conversation_id: every turn of one conversation lands in the same partition,
// so replaying a conversation is a single partition read (US6).
resource conversationsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'conversations'
  properties: {
    resource: {
      id: 'conversations'
      partitionKey: {
        paths: [ '/conversation_id' ]
        kind: 'Hash'
      }
      // Documents expire on their own, so no cleanup job and no unbounded growth.
      defaultTtl: conversationsTtlSeconds
    }
  }
}

// Data plane access. Azure RBAC roles such as Contributor do not grant document access here:
// Cosmos requires its own sqlRoleAssignments, which is a common source of 403 at runtime.
resource appDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, appPrincipalId, dataContributorRoleId)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleId}'
    principalId: appPrincipalId
    scope: account.id
  }
}

resource devDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (!empty(developerPrincipalId)) {
  parent: account
  name: guid(account.id, developerPrincipalId, dataContributorRoleId)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleId}'
    principalId: developerPrincipalId
    scope: account.id
  }
}

output id string = account.id
output name string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = databaseName
