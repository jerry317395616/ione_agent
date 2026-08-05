$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$SiteUrl = "__IONE_SITE_URL__"
$PairingToken = "__IONE_PAIRING_TOKEN__"
$Root = Join-Path $env:LOCALAPPDATA "I-ONE\Agent"
$Runtime = Join-Path $Root "runtime"
$UfoRoot = Join-Path $Runtime "UFO"
$Venv = Join-Path $Runtime ".venv"
$PythonVersion = "3.10"
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

function Read-DeviceConfig {
    $encrypted = (Get-Content -Raw $ConfigPath).Trim()
    $secure = ConvertTo-SecureString $encrypted
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) | ConvertFrom-Json)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Get-QueryParameter([string]$Url, [string]$Name) {
    $query = ([Uri]$Url).Query.TrimStart("?")
    foreach ($pair in $query.Split("&")) {
        $parts = $pair.Split("=", 2)
        if ($parts.Length -eq 2 -and $parts[0] -eq $Name) {
            return [Uri]::UnescapeDataString($parts[1])
        }
    }
    throw "The device connection URL does not contain $Name."
}

function Get-ModelApiBase([string]$ConnectionUrl) {
    $uri = [Uri]$ConnectionUrl
    $scheme = if ($uri.Scheme -eq "wss") { "https" } else { "http" }
    return "${scheme}://$($uri.Authority)/device/openai/v1"
}

function Write-UfoAgentConfig($Config) {
    $deviceToken = Get-QueryParameter $Config.connection_url "token"
    $agentCommon = [ordered]@{
        VISUAL_MODE = $false
        REASONING_MODEL = $false
        API_TYPE = "openai"
        API_BASE = $Config.model_api_base
        API_KEY = $deviceToken
        API_MODEL = $Config.model
    }
    $agents = [ordered]@{
        HOST_AGENT = $agentCommon + [ordered]@{
            PROMPT = "ufo/prompts/share/base/host_agent.yaml"
            EXAMPLE_PROMPT = "ufo/prompts/examples/{mode}/host_agent_example.yaml"
        }
        APP_AGENT = $agentCommon + [ordered]@{
            PROMPT = "ufo/prompts/share/base/app_agent.yaml"
            EXAMPLE_PROMPT = "ufo/prompts/examples/{mode}/app_agent_example.yaml"
            EXAMPLE_PROMPT_AS = "ufo/prompts/examples/{mode}/app_agent_example_as.yaml"
        }
        BACKUP_AGENT = $agentCommon
        EVALUATION_AGENT = $agentCommon
    }
    $configRoot = Join-Path $UfoRoot "config\ufo"
    New-Item -ItemType Directory -Force -Path $configRoot | Out-Null
    $agentsPath = Join-Path $configRoot "agents.yaml"
    $json = $agents | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($agentsPath, $json, (New-Object Text.UTF8Encoding($false)))
    & icacls.exe $agentsPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to secure the UFO model configuration." }
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
        client_version = "0.2.9"
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
        model_api_base = $response.message.model_api_base
        model = $response.message.model
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

$DeviceConfig = Read-DeviceConfig
if (-not $DeviceConfig.model_api_base -or -not $DeviceConfig.model) {
    $modelApiBase = Get-ModelApiBase $DeviceConfig.connection_url
    $modelInfo = Invoke-RestMethod -Uri (([Uri]$modelApiBase).GetLeftPart([UriPartial]::Authority) + "/health")
    $DeviceConfig | Add-Member -NotePropertyName model_api_base -NotePropertyValue $modelApiBase -Force
    $DeviceConfig | Add-Member -NotePropertyName model -NotePropertyValue $modelInfo.model -Force
    Protect-DeviceConfig @{
        device_id = $DeviceConfig.device_id
        device_name = $DeviceConfig.device_name
        connection_url = $DeviceConfig.connection_url
        model_api_base = $DeviceConfig.model_api_base
        model = $DeviceConfig.model
        site_url = $DeviceConfig.site_url
    }
}
Write-UfoAgentConfig $DeviceConfig

$Uv = Get-UvPath
Write-Host "Preparing Python $PythonVersion..."
& $Uv python install $PythonVersion
if ($LASTEXITCODE -ne 0) { throw "Unable to install Python $PythonVersion." }
$ExistingPython = Join-Path $Venv "Scripts\python.exe"
if (Test-Path $ExistingPython) {
    $ExistingVersion = (& $ExistingPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($ExistingVersion -ne $PythonVersion) {
        Write-Host "Replacing incompatible Python $ExistingVersion environment..."
        Remove-Item -Recurse -Force $Venv
    }
}
if (-not (Test-Path $ExistingPython)) {
    & $Uv venv --python $PythonVersion $Venv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the UFO environment." }
}

$Python = Join-Path $Venv "Scripts\python.exe"
Write-Host "Installing UFO Windows dependencies. This can take several minutes..."
& $Uv pip install --python $Python -r (Join-Path $UfoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Unable to install UFO dependencies." }

Copy-Item -Force (Join-Path $PSScriptRoot "launch.ps1") (Join-Path $Root "launch.ps1")
Copy-Item -Force (Join-Path $PSScriptRoot "uninstall.ps1") (Join-Path $Root "uninstall.ps1")

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -like "*$Root*" -and
    ($_.CommandLine -like "*launch.ps1*" -or $_.CommandLine -like "*ufo.client.client*")
} | Invoke-CimMethod -MethodName Terminate | Out-Null

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File `"$Root\launch.ps1`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "The device is registered and the desktop executor is starting."
Stop-Transcript | Out-Null
