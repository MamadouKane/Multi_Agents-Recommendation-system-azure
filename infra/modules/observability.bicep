// Dashboard, alerts and availability test of the deployed API (day 7, requirements section 10).
// Volumes are counted with sum(ItemCount), not count(): a sampled row stands for several
// requests, and the API sampled by default until day 7.
//
// The workbook is versioned as JSON (infra/workbooks/), the same file the portal shows. Alerts are
// log search rules on the workspace, so they read the same OpenTelemetry tables as the workbook.

param location string
param tags object

@description('Log Analytics workspace that holds the Application Insights tables.')
param workspaceId string

@description('Application Insights component name, which the availability test reports to.')
param appInsightsName string

@description('Public URL of the API, without a trailing slash.')
param apiUrl string

@description('E-mail address for alert notifications. Empty: alerts fire and show in the portal only.')
param alertEmail string = ''

@description('Seconds between two availability pings. Each ping wakes a scaled-to-zero replica, so 300 would keep it running around the clock (about 30 USD a month); 900 lets it sleep two thirds of the time.')
@allowed([ 300, 600, 900 ])
param availabilityFrequency int = 900

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'coffee-assistant-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'Coffee shop assistant'
    category: 'workbook'
    sourceId: workspaceId
    serializedData: replace(loadTextContent('../workbooks/coffee-assistant.workbook.json'), '__WORKSPACE_ID__', workspaceId)
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmail)) {
  name: 'ag-coffeeai-alerts'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'coffeeai'
    enabled: true
    emailReceivers: [
      {
        name: 'owner'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

var actions = empty(alertEmail) ? {} : { actionGroups: [ actionGroup.id ] }

// name, severity (0 critical .. 4 verbose), window, frequency, query returning rows only when the
// condition holds, description.
var rules = [
  {
    name: 'api-error-rate'
    severity: 2
    // 15 minutes, not the 5 of the requirements: telemetry lands 3 to 5 minutes late, so a 5-minute
    // window evaluated every 5 minutes let a real burst of 503s slip between two evaluations
    // (day 7 alert test). Each request is now seen by three evaluations.
    window: 'PT15M'
    frequency: 'PT5M'
    description: 'More than 5 % of API requests failed with a 5xx over 15 minutes (at least 5 requests).'
    query: 'AppRequests | where Name startswith "POST /api" or Name startswith "GET /api" | summarize total = sum(ItemCount), errors = sumif(ItemCount, toint(ResultCode) >= 500) | where total >= 5 and 100.0 * errors / total > 5'
  }
  {
    name: 'chat-latency-p95'
    severity: 2
    window: 'PT15M'
    frequency: 'PT5M'
    description: 'p95 of the chat endpoint above 8 s over 15 minutes (at least 5 requests).'
    query: 'AppRequests | where Name == "POST /api/v1/chat" | summarize total = sum(ItemCount), p95 = percentile(DurationMs, 95) | where total >= 5 and p95 > 8000'
  }
  {
    name: 'daily-model-cost'
    severity: 3
    window: 'P1D'
    frequency: 'PT1H'
    description: 'Model cost above 5 USD over the last 24 hours.'
    query: 'AppMetrics | where Name == "chat.cost.usd" | summarize usd = sum(Sum) | where usd > 5'
  }
  {
    name: 'order-total-mismatch'
    severity: 1
    window: 'PT15M'
    frequency: 'PT5M'
    description: 'A billed order total disagrees with the catalogue (OM4). Must never happen.'
    query: 'AppMetrics | where Name == "order.total.mismatch" | summarize n = sum(Sum) | where n > 0'
  }
  {
    name: 'api-unavailable'
    severity: 1
    window: 'PT30M'
    frequency: 'PT15M'
    description: 'The /health availability test failed.'
    query: 'AppAvailabilityResults | where Success == false'
  }
]

resource alerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [
  for rule in rules: {
    name: 'alert-coffeeai-${rule.name}'
    location: location
    tags: tags
    properties: {
      displayName: rule.name
      description: rule.description
      severity: rule.severity
      enabled: true
      scopes: [ workspaceId ]
      evaluationFrequency: rule.frequency
      windowSize: rule.window
      criteria: {
        allOf: [
          {
            query: rule.query
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
      actions: actions
    }
  }
]

resource availability 'Microsoft.Insights/webtests@2022-06-15' = {
  name: 'webtest-coffeeai-health'
  location: location
  // Links the test to the component, which is how its results land in Application Insights.
  tags: union(tags, { 'hidden-link:${appInsights.id}': 'Resource' })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: 'webtest-coffeeai-health'
    Name: 'API /health'
    Kind: 'standard'
    Enabled: true
    Frequency: availabilityFrequency
    Timeout: 120 // a cold start loads the catalogue and the recommender
    RetryEnabled: true
    Locations: [
      { Id: 'emea-fr-pra-edge' } // France Central
      { Id: 'emea-nl-ams-azr' }  // West Europe
    ]
    Request: {
      RequestUrl: '${apiUrl}/health'
      HttpVerb: 'GET'
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      SSLCheck: true
      SSLCertRemainingLifetimeCheck: 7
    }
  }
}

output workbookId string = workbook.id
