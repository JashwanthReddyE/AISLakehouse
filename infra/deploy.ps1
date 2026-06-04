<#
.SYNOPSIS
  Deploy AISLakehouse Week-1 infrastructure to a resource group.

.DESCRIPTION
  Creates the resource group (if missing) and deploys infra/main.bicep, which provisions the
  budget alert FIRST, then Event Hubs (Standard), ADLS Gen2, and Key Vault.

  COST NOTE: Event Hubs Standard tier bills ~$11/month if left running. Tear down between
  iterating sessions with:  az group delete -n <resourceGroup> --yes --no-wait

.EXAMPLE
  ./infra/deploy.ps1 -ResourceGroup aislakehouse-rg -Location eastus
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'aislakehouse-rg',
    [string]$Location = 'eastus'
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# az is a native exe — a non-zero exit does NOT trip $ErrorActionPreference, so check
# $LASTEXITCODE explicitly and stop, rather than plowing on with a misleading "Done".
function Invoke-Az {
    param([Parameter(Mandatory)][string[]]$AzArgs, [string]$FailMessage)
    az @AzArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error $FailMessage
        exit $LASTEXITCODE
    }
}

# Verify there is a live, non-expired login before doing anything else.
az account show --query id -o tsv 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error @'
Not signed in to Azure (or the session expired). Re-authenticate, then re-run this script:
  az login
'@
    exit 1
}

Write-Host "Ensuring resource group '$ResourceGroup' in '$Location'..."
Invoke-Az -AzArgs @(
    'group', 'create', '--name', $ResourceGroup, '--location', $Location,
    '--tags', 'project=aislakehouse'
) -FailMessage "Failed to create/ensure resource group '$ResourceGroup'." | Out-Null

Write-Host "Deploying Bicep (budget alert is created first)..."
Invoke-Az -AzArgs @(
    'deployment', 'group', 'create',
    '--resource-group', $ResourceGroup,
    '--template-file', "$here/main.bicep",
    '--parameters', "$here/main.bicepparam",
    '--parameters', "location=$Location"
) -FailMessage "Bicep deployment failed."

Write-Host ""
Write-Host "Done. Fetch the Send connection string for your local .env with:"
Write-Host "  az eventhubs eventhub authorization-rule keys list \"
Write-Host "    --resource-group $ResourceGroup --namespace-name <ns> \"
Write-Host "    --eventhub-name ais-raw --name ingest-send --query primaryConnectionString -o tsv"
Write-Host ""
Write-Host "TEARDOWN when done iterating (stops Event Hubs Standard billing):"
Write-Host "  az group delete -n $ResourceGroup --yes --no-wait"
