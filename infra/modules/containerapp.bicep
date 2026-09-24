// Container Apps environment and the API container app.
// Day 1 runs a public placeholder image: the real one is built and pushed on day 6, and the
// deployment then only swaps the image tag on a new revision.

@description('Managed environment name.')
param environmentName string

@description('Container app name.')
param appName string

param location string
param tags object

@description('Log Analytics workspace resource id, used by the diagnostic setting.')
param logAnalyticsWorkspaceId string

@description('Application Insights component name. Its connection string is read inside this module.')
param appInsightsName string

@description('Resource id of the user-assigned managed identity attached to the app.')
param managedIdentityId string

@description('Client id of that identity. DefaultAzureCredential needs it to pick the right one.')
param managedIdentityClientId string

@description('Placeholder image until the real one exists in the registry (day 6).')
param image string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Port the container listens on. The placeholder image serves on 80.')
param targetPort int = 80

@description('Endpoints injected as environment variables, so the app never hardcodes a URL.')
param serviceEndpoints object

@description('Origins allowed to call the API. No wildcard, which clears debt D5.')
param corsAllowedOrigins string = 'http://localhost:3000'

// Reading the connection string here, rather than passing it around as a template output,
// keeps it out of deployment history and out of `az deployment group show`.
resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    // azure-monitor sends logs through the diagnostic setting below, which needs no shared key.
    // The alternative, log-analytics, would require the workspace key inside the template.
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    zoneRedundant: false
  }
}

resource environmentDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'send-to-log-analytics'
  scope: environment
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'ContainerAppConsoleLogs'
        enabled: true
      }
      {
        category: 'ContainerAppSystemLogs'
        enabled: true
      }
    ]
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    // User-assigned: the identity outlives the app, so its role assignments are not lost
    // when the app is recreated.
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        // Reachable from the internet, over managed TLS.
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      // Multiple revisions is what makes blue/green deployments possible on day 6.
      activeRevisionsMode: 'Multiple'
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            // DefaultAzureCredential reads this to choose the user-assigned identity.
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentityClientId
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: serviceEndpoints.openAi
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
              value: serviceEndpoints.chatDeployment
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: serviceEndpoints.embeddingDeployment
            }
            {
              name: 'AZURE_COSMOS_ENDPOINT'
              value: serviceEndpoints.cosmos
            }
            {
              name: 'AZURE_COSMOS_DATABASE'
              value: serviceEndpoints.cosmosDatabase
            }
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: serviceEndpoints.search
            }
            {
              name: 'AZURE_STORAGE_BLOB_ENDPOINT'
              value: serviceEndpoints.storageBlob
            }
            {
              name: 'CORS_ALLOWED_ORIGINS'
              value: corsAllowedOrigins
            }
          ]
        }
      ]
      scale: {
        // Zero replicas when idle: nothing is billed while nobody calls the API (NFR5 trade-off).
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output environmentId string = environment.id
output appName string = app.name

@description('Public hostname of the app, used by the day 6 smoke tests.')
output fqdn string = app.properties.configuration.ingress.fqdn
