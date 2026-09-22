// Built-in Azure role definition ids, in one place.
// They are identical in every tenant, and they are checked in with the command that produced them,
// so a typo is caught by reading rather than by a failed deployment:
//   az role definition list --name "<role name>" --query "[0].name" -o tsv
// Verified on 2026-09-22.

@export()
@description('Built-in role definition ids used by this workload.')
var roleIds = {
  // Data plane: read blob contents. Control plane roles such as Owner do not grant this.
  storageBlobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

  // Container registry.
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'

  // Key Vault, RBAC model.
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'  // gitleaks:allow (public Azure role id, not a secret)
  keyVaultSecretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'  // gitleaks:allow (public Azure role id, not a secret)

  // Azure OpenAI and other Azure AI services.
  cognitiveServicesOpenAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'

  // Azure AI Search: querying an index and managing the service are two different roles.
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

  // Monitoring.
  monitoringMetricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
}
