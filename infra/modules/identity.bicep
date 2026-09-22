// User-assigned managed identity.
// It is the single principal that holds every RBAC role of the workload (objective OT2),
// and it survives redeployments of the Container App, unlike a system-assigned identity.

@description('Managed identity name.')
param name string

param location string
param tags object

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

@description('Resource id, used when attaching the identity to a Container App.')
output id string = uami.id

@description('Object id of the service principal, used as the assignee of RBAC role assignments.')
output principalId string = uami.properties.principalId

@description('Client id, read by DefaultAzureCredential when several identities are attached.')
output clientId string = uami.properties.clientId

output name string = uami.name
