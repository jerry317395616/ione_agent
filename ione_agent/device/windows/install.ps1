$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$SiteUrl = "__IONE_SITE_URL__"
$PairingToken = "__IONE_PAIRING_TOKEN__"
$Root = Join-Path $env:LOCALAPPDATA "I-ONE\Agent"
$Runtime = Join-Path $Root "runtime"
$UfoRoot = Join-Path $Runtime "UFO"
$Venv = Join-Path $Runtime ".venv"
$ConfigPath = Join-Path $Root "device.config"
$LogRoot = Join-Path $Root "logs"
$TaskName = "I-ONE Agent Device"

New-Item -ItemType Directory -Force -Path $Root, $Runtime, $LogRoot | Out-Null
Start-Transcript -Path (Join-Path $LogRoot "install.log") -Append | Out-Null

function Protect-DeviceConfig([hashtable]$Config) {
    $json = $Config | ConvertTo-Json -Compress
    $secure = ConvertTo-SecureString $json -AsPlainText -Force
    $secure | ConvertFrom-SecureString | Set-Content -Path $ConfigPath -Encoding UTF8
    & icacls.exe $ConfigPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to secure the device configuration." }
}

function Get-UvPath {
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    Write-Host "Installing the Python environment manager..."
    $installer = Invoke-RestMethod "https://astral.sh/uv/install.ps1"
    Invoke-Expression ([string]$installer)
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "uv installation did not produce an executable."
}

if (-not (Test-Path $ConfigPath)) {
    $DeviceId = "windows-" + ([guid]::NewGuid().ToString("N"))
    $Body = @{
        pairing_token = $PairingToken
        device_id = $DeviceId
        device_name = $env:COMPUTERNAME
        client_version = "0.2.2"
    }
    Write-Host "Pairing this computer with I-ONE Agent..."
    $response = Invoke-RestMethod `
        -Method Post `
        -Uri "$SiteUrl/api/method/ione_agent.device_api.claim_pairing" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $Body
    Protect-DeviceConfig @{
        device_id = $response.message.device_id
        device_name = $response.message.device_name
        connection_url = $response.message.connection_url
        site_url = $SiteUrl
    }
}

$Git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $Git) {
    $Git = Join-Path $env:ProgramFiles "Git\cmd\git.exe"
}
if (-not (Test-Path $Git)) {
    throw "Git is required. Install Git for Windows and run this setup again."
}

if (-not (Test-Path (Join-Path $UfoRoot ".git"))) {
    Write-Host "Downloading Microsoft UFO main branch..."
    & $Git clone --depth 1 --branch main https://github.com/microsoft/UFO.git $UfoRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to clone Microsoft UFO." }
} else {
    Write-Host "Updating Microsoft UFO main branch..."
    & $Git -C $UfoRoot pull --ff-only origin main
    if ($LASTEXITCODE -ne 0) { throw "Unable to update Microsoft UFO." }
}

$Uv = Get-UvPath
Write-Host "Preparing Python 3.11..."
& $Uv python install 3.11
if ($LASTEXITCODE -ne 0) { throw "Unable to install Python 3.11." }
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    & $Uv venv --python 3.11 $Venv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the UFO environment." }
}

$Python = Join-Path $Venv "Scripts\python.exe"
Write-Host "Installing UFO Windows dependencies. This can take several minutes..."
& $Uv pip install --python $Python -r (Join-Path $UfoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Unable to install UFO dependencies." }

Copy-Item -Force (Join-Path $PSScriptRoot "launch.ps1") (Join-Path $Root "launch.ps1")
Copy-Item -Force (Join-Path $PSScriptRoot "uninstall.ps1") (Join-Path $Root "uninstall.ps1")

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File `"$Root\launch.ps1`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "The device is registered and the desktop executor is starting."
Stop-Transcript | Out-Null
