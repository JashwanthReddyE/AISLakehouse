// AISLakehouse — Week 1 infrastructure (resource-group scoped).
//
// IMPORTANT: the budget + alert is declared FIRST and has no dependency on the billable
// resources, so it exists from the moment the deployment starts. Cost discipline is the point.
//
// Provisions: monthly budget alert, Event Hubs (Standard — required for the Kafka endpoint),
// ADLS Gen2 (hierarchical namespace), and Key Vault holding the connection string + AIS key.

targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short prefix for resource names. Lowercase alphanumerics.')
@minLength(3)
@maxLength(11)
param namePrefix string = 'aislake'

@description('Email address to receive budget alerts.')
param alertEmail string

@description('Monthly budget ceiling in USD. Alerts fire at 50/90/100%.')
param budgetAmount int = 10

@description('Budget start date — must be the first of a month, format YYYY-MM-DD.')
param budgetStartDate string = '2026-06-01'

@description('Event hub (topic) name. Must match EVENTHUB_NAME in .env.')
param eventHubName string = 'ais-raw'

@description('Lakehouse blob container name.')
param lakeContainerName string = 'lakehouse'

@description('AISStream API key. Leave empty to set the Key Vault secret manually later.')
@secure()
param aisStreamApiKey string = ''

@description('Object ID (principal) granted Key Vault secret access. Defaults to the deployer.')
param keyVaultAdminObjectId string = ''

var suffix = uniqueString(resourceGroup().id)
var namespaceName = '${namePrefix}-ehns-${suffix}'
// Storage account names are max 24 chars, lowercase alphanumerics only.
var storageName = take(toLower('${namePrefix}st${suffix}'), 24)
var keyVaultName = '${namePrefix}-kv-${suffix}'
var commonTags = {
  project: 'aislakehouse'
  layer: 'week1-ingest'
  managedBy: 'bicep'
}

// ----------------------------------------------------------------------------
// 1. BUDGET FIRST — no dependsOn, so it is created before anything can bill.
// ----------------------------------------------------------------------------
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${namePrefix}-monthly-budget'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      actual_50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        contactEmails: [alertEmail]
        thresholdType: 'Actual'
      }
      actual_90: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 90
        contactEmails: [alertEmail]
        thresholdType: 'Actual'
      }
      forecast_100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        contactEmails: [alertEmail]
        thresholdType: 'Forecasted'
      }
    }
  }
}

// ----------------------------------------------------------------------------
// 2. Event Hubs (Standard tier — Kafka endpoint requires Standard or higher).
// ----------------------------------------------------------------------------
resource ehNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: namespaceName
  location: location
  tags: commonTags
  sku: {
    name: 'Standard'
    tier: 'Standard'
    capacity: 1
  }
  properties: {
    minimumTlsVersion: '1.2'
    kafkaEnabled: true
  }
}

resource eventHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehNamespace
  name: eventHubName
  properties: {
    partitionCount: 2
    messageRetentionInDays: 1
  }
}

// Send-only rule for the local consumer.
resource sendRule 'Microsoft.EventHub/namespaces/eventhubs/authorizationRules@2024-01-01' = {
  parent: eventHub
  name: 'ingest-send'
  properties: {
    rights: ['Send']
  }
}

// Listen rule for Databricks bronze stream.
resource listenRule 'Microsoft.EventHub/namespaces/eventhubs/authorizationRules@2024-01-01' = {
  parent: eventHub
  name: 'bronze-listen'
  properties: {
    rights: ['Listen']
  }
}

// ----------------------------------------------------------------------------
// 3. ADLS Gen2 — StorageV2 + hierarchical namespace.
// ----------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: commonTags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource lakeContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageName}/default/${lakeContainerName}'
  dependsOn: [storage]
}

// ----------------------------------------------------------------------------
// 4. Key Vault — connection string (for Databricks/Week-4) + AIS key.
// ----------------------------------------------------------------------------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: commonTags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: false
    accessPolicies: empty(keyVaultAdminObjectId) ? [] : [
      {
        tenantId: subscription().tenantId
        objectId: keyVaultAdminObjectId
        permissions: {
          secrets: ['get', 'list', 'set']
        }
      }
    ]
  }
}

resource secretListenConn 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'eventhub-listen-connection-string'
  properties: {
    value: listenRule.listKeys().primaryConnectionString
  }
}

resource secretAisKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(aisStreamApiKey)) {
  parent: keyVault
  name: 'aisstream-api-key'
  properties: {
    value: aisStreamApiKey
  }
}

// ----------------------------------------------------------------------------
// Outputs (no secrets — fetch connection strings via `az ... keys list`).
// ----------------------------------------------------------------------------
output eventHubsNamespaceFqdn string = '${namespaceName}.servicebus.windows.net'
output eventHubName string = eventHubName
output storageAccountName string = storageName
output lakeContainerName string = lakeContainerName
output keyVaultName string = keyVaultName
// Convenience string for the Databricks widget; suffix is fixed for the public Azure cloud.
#disable-next-line no-hardcoded-env-urls
output bronzePath string = 'abfss://${lakeContainerName}@${storageName}.dfs.core.windows.net/bronze/ais_raw'
