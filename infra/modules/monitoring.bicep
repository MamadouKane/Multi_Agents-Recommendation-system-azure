// Log Analytics workspace plus a workspace-based Application Insights component.
// Application Insights is the OpenTelemetry endpoint for the API (objective OT4).

@description('Log Analytics workspace name.')
param workspaceName string

@description('Application Insights component name.')
param appInsightsName string

param location string
param tags object

@minValue(30)
@maxValue(730)
param retentionInDays int = 30

@description('Daily ingestion cap in GB. Ingestion stops for the day once the cap is reached.')
param dailyQuotaGb int = 1

@description('Principal id of the API managed identity, allowed to send telemetry.')
param appPrincipalId string

@description('Principal id of the developer running the API locally. Empty to skip.')
param developerPrincipalId string = ''

import { roleIds } from '../shared/roles.bicep'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    // PerGB2018 is the only pay-as-you-go SKU. The first 5 GB per month are free.
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    workspaceCapping: {
      dailyQuotaGb: dailyQuotaGb
    }
    features: {
      // Read access is granted by RBAC on the resource, not by the workspace keys.
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    // Flow_Type and Request_Source are metadata that ARM fills in by itself. Setting them here
    // keeps `what-if` free of a permanent phantom change on this resource.
    Flow_Type: 'Bluefield'
    Request_Source: 'rest'
    // Workspace-based component: telemetry lands in Log Analytics and is queried with KQL.
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    // Keep the last IP octet masked, so no client address is stored (NFR11).
    DisableIpMasking: false
    // Entra ID only: telemetry is accepted from identities holding Monitoring Metrics Publisher.
    // The connection string then only names the target; on its own it can no longer write.
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appTelemetryPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, appPrincipalId, roleIds.monitoringMetricsPublisher)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.monitoringMetricsPublisher)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource devTelemetryPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(appInsights.id, developerPrincipalId, roleIds.monitoringMetricsPublisher)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.monitoringMetricsPublisher)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
// Not a secret with local auth disabled: it names the target, the identity does the writing.
output connectionString string = appInsights.properties.ConnectionString
