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
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
