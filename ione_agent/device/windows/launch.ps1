$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "I-ONE\Agent"
$UfoRoot = Join-Path $Root "runtime\UFO"
$Python = Join-Path $Root "runtime\.venv\Scripts\python.exe"
$ConfigPath = Join-Path $Root "device.config"
$LogRoot = Join-Path $Root "logs"

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

function Start-UfoClient($Config) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return Start-Process `
        -FilePath $Python `
        -ArgumentList @(
            "-m", "ufo.client.client",
            "--ws",
            "--ws-server", $Config.connection_url,
            "--client-id", $Config.device_id,
            "--platform", "windows",
            "--max-retries", "1000000",
            "--log-level", "WARNING"
        ) `
        -WorkingDirectory $UfoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "client-$stamp.out.log") `
        -RedirectStandardError (Join-Path $LogRoot "client-$stamp.error.log") `
        -PassThru
}

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$Config = Read-DeviceConfig
$ClientProcess = Start-UfoClient $Config

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$Tray = New-Object System.Windows.Forms.NotifyIcon
$Tray.Icon = [System.Drawing.SystemIcons]::Application
$Tray.Text = "I-ONE Agent Device"
$Tray.Visible = $true
$Menu = New-Object System.Windows.Forms.ContextMenuStrip
$Status = $Menu.Items.Add("Connected to I-ONE Agent")
$Status.Enabled = $false
$Reconnect = $Menu.Items.Add("Reconnect")
$Exit = $Menu.Items.Add("Exit")
$Tray.ContextMenuStrip = $Menu

$Reconnect.add_Click({
    if ($ClientProcess -and -not $ClientProcess.HasExited) { $ClientProcess.Kill() }
    $script:ClientProcess = Start-UfoClient $Config
})
$Exit.add_Click({
    if ($ClientProcess -and -not $ClientProcess.HasExited) { $ClientProcess.Kill() }
    $Tray.Visible = $false
    [System.Windows.Forms.Application]::Exit()
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 5000
$Timer.add_Tick({
    if (-not $ClientProcess -or $ClientProcess.HasExited) {
        $script:ClientProcess = Start-UfoClient $Config
    }
})
$Timer.Start()
[System.Windows.Forms.Application]::Run()
