<#
.SYNOPSIS
    Read-only export of the source tenant's devices and users to the devices.csv / users.csv format used by the cutover planner.

.DESCRIPTION
    Uses the Microsoft Graph PowerShell SDK (and optionally Exchange Online) to build two CSV files:

      devices.csv  one row per Intune-managed Windows device: name, serial, primary user, model, OS version, join type,
                   management agent, Autopilot registration (matched by serial against the Autopilot device list),
                   BitLocker key escrow (matched by device id against the recovery keys in Entra), compliance, last sync.
      users.csv    one row per member user: UPN, display name, department, office, manager, licences, MFA registration,
                   last sign-in, account type; mailbox size if -IncludeMailboxes.

    It only reads. Sign in with an account that can read devices, users and (optionally) mailbox statistics; nothing is
    written to either tenant and no credential is stored. TPM / Secure Boot / RAM / free disk are not in Graph by default:
    export them from your Windows 11 readiness report (Intune > Reports > Windows > Windows 11 hardware readiness),
    from Endpoint analytics, or from ConfigMgr hardware inventory, and merge them on the device name. Without those
    columns the planner assumes the hardware is capable, so merge them before relying on the Windows 11 check (DEV-03).

    Site, VIP, Leaving, Installed apps and Shared mailboxes are usually better taken from HR and the app inventory;
    the script leaves those columns empty for you to fill.

.PARAMETER OutputFolder
    Where to write devices.csv and users.csv. Default: .\data

.PARAMETER IncludeMailboxes
    Also connect to Exchange Online and add mailbox size (GB) per user. Slower on large tenants.

.EXAMPLE
    .\scripts\Export-TenantInventory.ps1 -OutputFolder .\data -IncludeMailboxes
    python -m cutover plan .\data --title "Project Lighthouse" --as-of 2026-10-23
#>
[CmdletBinding()]
param(
    [string] $OutputFolder = '.\data',
    [switch] $IncludeMailboxes
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null

# ---- Graph (read-only scopes) --------------------------------------------------------------------------------------
Import-Module Microsoft.Graph.Authentication
Connect-MgGraph -Scopes 'DeviceManagementManagedDevices.Read.All', 'DeviceManagementServiceConfig.Read.All',
                        'Device.Read.All', 'User.Read.All', 'Directory.Read.All', 'BitLockerKey.ReadBasic.All',
                        'UserAuthenticationMethod.Read.All', 'AuditLog.Read.All' -NoWelcome

function Get-AllPages([string] $Uri) {
    $out = @()
    while ($Uri) {
        $r = Invoke-MgGraphRequest -Method GET -Uri $Uri
        $out += $r.value
        $Uri = $r.'@odata.nextLink'
    }
    return $out
}

Write-Host 'Autopilot devices...'
$autopilot = @{}
foreach ($a in Get-AllPages 'https://graph.microsoft.com/beta/deviceManagement/windowsAutopilotDeviceIdentities?$select=serialNumber') {
    if ($a.serialNumber) { $autopilot[$a.serialNumber.Trim().ToUpper()] = $true }
}

Write-Host 'BitLocker recovery keys (device ids only)...'
$bitlocker = @{}
foreach ($k in Get-AllPages 'https://graph.microsoft.com/v1.0/informationProtection/bitlocker/recoveryKeys?$select=id,deviceId') {
    if ($k.deviceId) { $bitlocker[$k.deviceId] = $true }
}

Write-Host 'Entra devices (join type)...'
$join = @{}
foreach ($d in Get-AllPages 'https://graph.microsoft.com/v1.0/devices?$select=deviceId,trustType,displayName&$filter=operatingSystem eq ''Windows''') {
    $join[$d.deviceId] = switch ($d.trustType) { 'ServerAd' { 'Hybrid Azure AD joined' } 'AzureAd' { 'Azure AD joined' } 'Workplace' { 'Azure AD registered' } default { $d.trustType } }
}

Write-Host 'Intune managed devices...'
$devices = foreach ($m in Get-AllPages ('https://graph.microsoft.com/beta/deviceManagement/managedDevices?$filter=operatingSystem eq ''Windows''' +
                                        '&$select=deviceName,serialNumber,userPrincipalName,model,osVersion,managementAgent,complianceState,lastSyncDateTime,azureADDeviceId,joinType,physicalMemoryInBytes,freeStorageSpaceInBytes')) {
    $serial = if ($m.serialNumber) { $m.serialNumber.Trim().ToUpper() } else { '' }
    [pscustomobject]@{
        'Device name'            = $m.deviceName
        'Serial number'          = $m.serialNumber
        'Primary user UPN'       = $m.userPrincipalName
        'Site'                   = ''
        'Model'                  = $m.model
        'OS version'             = $m.osVersion
        'TPM 2.0'                = ''
        'Secure Boot'            = ''
        'RAM GB'                 = if ($m.physicalMemoryInBytes) { [math]::Round($m.physicalMemoryInBytes / 1GB) } else { '' }
        'Free disk GB'           = if ($m.freeStorageSpaceInBytes) { [math]::Round($m.freeStorageSpaceInBytes / 1GB) } else { '' }
        'Join type'              = if ($join.ContainsKey($m.azureADDeviceId)) { $join[$m.azureADDeviceId] } else { $m.joinType }
        'Management agent'       = $m.managementAgent
        'Autopilot registered'   = if ($serial -and $autopilot.ContainsKey($serial)) { 'Yes' } else { 'No' }
        'BitLocker key escrowed' = if ($m.azureADDeviceId -and $bitlocker.ContainsKey($m.azureADDeviceId)) { 'Yes' } else { 'No' }
        'Compliance state'       = $m.complianceState
        'Last sync'              = if ($m.lastSyncDateTime) { ([datetime]$m.lastSyncDateTime).ToString('yyyy-MM-dd HH:mm') } else { '' }
        'Local admins'           = ''
        'Installed apps'         = ''
        'VIP'                    = ''
        'Device type'            = ''
    }
}
$devices | Export-Csv -Path (Join-Path $OutputFolder 'devices.csv') -NoTypeInformation -Encoding UTF8
Write-Host "devices.csv: $($devices.Count) rows"

Write-Host 'Licence SKU names...'
$skuNames = @{}
foreach ($s in Get-AllPages 'https://graph.microsoft.com/v1.0/subscribedSkus?$select=skuId,skuPartNumber') { $skuNames[$s.skuId] = $s.skuPartNumber }

Write-Host 'Users, MFA registration and last sign-in...'
$mfa = @{}
foreach ($r in Get-AllPages 'https://graph.microsoft.com/beta/reports/authenticationMethods/userRegistrationDetails?$select=userPrincipalName,isMfaRegistered') {
    $mfa[$r.userPrincipalName.ToLower()] = [bool]$r.isMfaRegistered
}

$mailbox = @{}
if ($IncludeMailboxes) {
    Write-Host 'Mailbox sizes (Exchange Online)...'
    Import-Module ExchangeOnlineManagement
    Connect-ExchangeOnline -ShowBanner:$false
    Get-EXOMailbox -ResultSize Unlimited -RecipientTypeDetails UserMailbox -Properties UserPrincipalName |
        Get-EXOMailboxStatistics -Properties TotalItemSize | ForEach-Object {
            $bytes = [double](($_.TotalItemSize.ToString() -replace '.*\(([\d,]+) bytes\).*', '$1') -replace ',', '')
            $mailbox[$_.MailboxOwnerId.ToString().ToLower()] = [math]::Round($bytes / 1GB, 1)
        }
    Disconnect-ExchangeOnline -Confirm:$false
}

$users = foreach ($u in Get-AllPages ('https://graph.microsoft.com/v1.0/users?$filter=userType eq ''Member'' and accountEnabled eq true' +
                                      '&$select=userPrincipalName,displayName,department,officeLocation,assignedLicenses,signInActivity,userType&$expand=manager($select=userPrincipalName)')) {
    $upn = $u.userPrincipalName.ToLower()
    [pscustomobject]@{
        'User principal name' = $u.userPrincipalName
        'Display name'        = $u.displayName
        'Department'          = $u.department
        'Office location'     = $u.officeLocation
        'Manager'             = $u.manager.userPrincipalName
        'Mailbox size GB'     = if ($mailbox.ContainsKey($upn)) { $mailbox[$upn] } else { '' }
        'OneDrive GB'         = ''
        'Assigned licenses'   = (($u.assignedLicenses | ForEach-Object { if ($skuNames.ContainsKey($_.skuId)) { $skuNames[$_.skuId] } else { $_.skuId } }) -join '; ')
        'MFA registered'      = if ($mfa.ContainsKey($upn)) { if ($mfa[$upn]) { 'Yes' } else { 'No' } } else { '' }
        'Shared mailboxes'    = ''
        'Last sign-in'        = if ($u.signInActivity.lastSignInDateTime) { ([datetime]$u.signInActivity.lastSignInDateTime).ToString('yyyy-MM-dd HH:mm') } else { '' }
        'VIP'                 = ''
        'Leaving'             = ''
        'Account type'        = 'member'
    }
}
$users | Export-Csv -Path (Join-Path $OutputFolder 'users.csv') -NoTypeInformation -Encoding UTF8
Write-Host "users.csv: $($users.Count) rows"
Write-Host "Now add settings.json (waves, freeze, app_catalog, licence_map, prerequisites) and run: python -m cutover plan $OutputFolder"
