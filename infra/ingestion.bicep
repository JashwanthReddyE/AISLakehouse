// AISLakehouse — always-on ingestion on Azure Container Apps (Week 4).
//
// Runs the WebSocket consumer 24/7 (min replicas = 1, no HTTP ingress — it's a worker).
// Deploy AFTER the image exists in ACR (see infra/deploy_ingestion.ps1, which builds it).

targetScope = 'resourceGroup'

param location string = resourceGroup().location
param namePrefix string = 'aislake'

@description('ACR login server, e.g. aislakeacrxxxx.azurecr.io')
param acrLoginServer string
@description('ACR admin username')
param acrUsername string
@secure()
param acrPassword string
@description('Full image reference, e.g. aislakeacrxxxx.azurecr.io/aislake-ingest:latest')
param image string

@secure()
param aisStreamApiKey string
@secure()
param eventHubConnectionString string
param aisBbox string = '1.05,103.5,1.45,104.1'
param eventHubName string = 'ais-raw'

var tags = { project: 'aislakehouse', layer: 'week4-ingestion', managedBy: 'bicep' }

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-cae'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-ingest'
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: [
        { name: 'acr-password', value: acrPassword }
        { name: 'ais-key', value: aisStreamApiKey }
        { name: 'eh-conn', value: eventHubConnectionString }
      ]
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ingest'
          image: image
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'AISSTREAM_API_KEY', secretRef: 'ais-key' }
            { name: 'EVENTHUB_CONNECTION_STRING', secretRef: 'eh-conn' }
            { name: 'AIS_BBOX', value: aisBbox }
            { name: 'EVENTHUB_NAME', value: eventHubName }
          ]
        }
      ]
      // Always-on single worker; this is a continuous consumer, not a scalable web service.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output appName string = app.name
output environmentName string = env.name
