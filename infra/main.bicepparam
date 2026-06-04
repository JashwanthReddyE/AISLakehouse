using './main.bicep'

// Fill these in before deploying. alertEmail is required.
param alertEmail = 'jashwanthreddyearla@gmail.com'
param namePrefix = 'aislake'
param budgetAmount = 10
param budgetStartDate = '2026-06-01'
param eventHubName = 'ais-raw'
param lakeContainerName = 'lakehouse'

// Optional: set the AISStream key here to push it into Key Vault at deploy time,
// or leave empty and set the secret manually with `az keyvault secret set`.
param aisStreamApiKey = ''

// Optional: object id (e.g. your user) to grant Key Vault secret access.
// Get it with: az ad signed-in-user show --query id -o tsv
param keyVaultAdminObjectId = ''
