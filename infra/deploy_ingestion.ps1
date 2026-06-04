<#
.SYNOPSIS
  Deploy the always-on AIS consumer to Azure Container Apps (Week 4).

.DESCRIPTION
  1. Creates an Azure Container Registry (Basic) tied to the existing deployment suffix.
  2. Builds + pushes the ingestion image in the cloud (az acr build — no local Docker needed).
  3. Deploys infra/ingestion.bicep (Log Analytics + Container Apps env + always-on app),
     wiring the AIS key + Event Hubs Send connection string as Container App secrets.

  COST: Container Apps (0.25 vCPU, always-on) + Event Hubs Standard ~ a few CAD/week.
  Teardown: az group delete -n <rg> --yes --no-wait

.EXAMPLE
  ./infra/deploy_ingestion.ps1 -ResourceGroup aislakehouse-rg -Location eastus
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'aislakehouse-rg',
    [string]$Location = 'eastus',
    [string]$NamePrefix = 'aislake'
)

$ErrorActionPreference = 'Stop'
# az acr build streams the live build log; the pip progress bar contains Unicode that crashes a
# cp1252 console. Force UTF-8 so streaming doesn't kill the client mid-build.
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

function Stop-OnError($msg) { if ($LASTEXITCODE -ne 0) { Write-Error $msg; exit $LASTEXITCODE } }

az account show --query id -o tsv 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "Not signed in. Run 'az login' and retry."; exit 1 }

# Derive the deployment suffix from the existing storage account so names line up.
$storage = az storage account list -g $ResourceGroup --query "[?starts_with(name,'${NamePrefix}st')].name | [0]" -o tsv
Stop-OnError "Could not list storage accounts in $ResourceGroup (is the base infra deployed?)."
if (-not $storage) { Write-Error "No '${NamePrefix}st*' storage account found. Deploy infra/main.bicep first."; exit 1 }
$suffix = $storage -replace "^${NamePrefix}st", ''
$acrName = "${NamePrefix}acr${suffix}"
$acrServer = "$acrName.azurecr.io"
$image = "$acrServer/aislake-ingest:latest"

Write-Host "Creating ACR '$acrName'..."
az acr create -n $acrName -g $ResourceGroup --sku Basic --admin-enabled true -o none
Stop-OnError "ACR create failed."

Write-Host "Building + pushing image in the cloud (az acr build)..."
az acr build -r $acrName -t aislake-ingest:latest "$root" -o none
Stop-OnError "Image build failed."

$acrUser = az acr credential show -n $acrName --query username -o tsv
$acrPass = az acr credential show -n $acrName --query "passwords[0].value" -o tsv
Stop-OnError "Could not read ACR credentials."

# Secrets for the container: AIS key (from .env) + Event Hubs Send connection string (from Azure).
$envFile = Join-Path $root '.env'
$aisKey = (Get-Content $envFile | Where-Object { $_ -match '^\s*AISSTREAM_API_KEY\s*=' }) -replace '^\s*AISSTREAM_API_KEY\s*=\s*', ''
if (-not $aisKey) { Write-Error "AISSTREAM_API_KEY not found in $envFile"; exit 1 }
$aisBbox = (Get-Content $envFile | Where-Object { $_ -match '^\s*AIS_BBOX\s*=' }) -replace '^\s*AIS_BBOX\s*=\s*', ''
if (-not $aisBbox) { $aisBbox = '1.05,103.5,1.45,104.1' }

$ehNs = az eventhubs namespace list -g $ResourceGroup --query "[0].name" -o tsv
$ehConn = az eventhubs eventhub authorization-rule keys list `
    --resource-group $ResourceGroup --namespace-name $ehNs `
    --eventhub-name ais-raw --name ingest-send --query primaryConnectionString -o tsv
Stop-OnError "Could not fetch Event Hubs Send connection string."

Write-Host "Deploying Container App (always-on consumer)..."
az deployment group create `
    --resource-group $ResourceGroup `
    --template-file "$here/ingestion.bicep" `
    --parameters location=$Location namePrefix=$NamePrefix `
    --parameters acrLoginServer=$acrServer acrUsername=$acrUser acrPassword=$acrPass image=$image `
    --parameters aisStreamApiKey=$aisKey eventHubConnectionString=$ehConn `
    --parameters "aisBbox=$aisBbox" `
    -o none
Stop-OnError "Container App deployment failed."

Write-Host ""
Write-Host "Deployed. Check it's running:"
Write-Host "  az containerapp show -g $ResourceGroup -n ${NamePrefix}-ingest --query properties.runningStatus -o tsv"
Write-Host "  az containerapp logs show -g $ResourceGroup -n ${NamePrefix}-ingest --tail 30"
