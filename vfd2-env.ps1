#Requires -Version 5.0
# EnvPilot - VFD2 Console GUI; backend in Python (vfd2_env_backend.py)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$script:Root        = $PSScriptRoot
$script:BackendPy   = Join-Path $script:Root 'vfd2_env_backend.py'
$script:SessionId   = Get-Date -Format 'yyyyMMdd_HHmmss'
$script:PythonExe       = $null
$script:LogDir          = Join-Path $script:Root 'logs'
if (-not (Test-Path -LiteralPath $script:LogDir)) {
    New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
}
$script:LogFile         = Join-Path $script:LogDir "vfd2_env_$($script:SessionId).log"
$script:CitrixId        = ''
$script:CitrixPassword  = ''
$script:CitrixIdFile    = Join-Path $script:Root 'citrix-id.txt'
$script:PuttyUser       = ''
$script:PuttyPassword   = ''
$script:PuttyCredFile   = Join-Path $script:Root 'putty-credentials.json'
$script:JenkinsUploadDir = Join-Path $script:Root ' Jenkins Deployment Script'
$script:JenkinsUploadCredFile = Join-Path $script:Root 'jenkins-upload-credentials.json'
$script:SelectedSitTarget = ''
$script:JenkinsUploadValues = @{}
$script:DeployRoot          = Split-Path $script:Root -Parent
$script:DeployBat           = Join-Path $script:DeployRoot 'deploy.bat'
$script:DeployCsvPath       = Join-Path $script:DeployRoot 'deploy-data.csv'
$script:DeployFormFields    = @{}
$script:DeployTimingMode    = ''
$script:ScheduledDeployTime = $null
$script:DeployAlwaysEmpty   = @('Release_name', 'ENVIRONMENT_NAME', 'FOLDER_NAME')
$script:DeployAlwaysDefault = @('BBs_TO_DEPLOY')
$script:DeployFieldLabels   = [ordered]@{
    Username         = 'Username'
    Password         = 'Password'
    ProjectName      = 'ProjectName'
    Release_name     = 'Release_name'
    BBs_TO_DEPLOY    = 'BBs TO_DEPLOY'
    ENVIRONMENT_NAME = 'ENVIRONMENT_NAME'
    FOLDER_NAME      = 'FOLDER_NAME'
    BuildNumber      = 'BuildNumber'
    BuildNummm       = 'BuildNummm'
}
$script:DeployDefaults = [ordered]@{
    Username         = 'sudhansh'
    Password         = ''
    ProjectName      = 'OGW'
    Release_name     = ''
    BBs_TO_DEPLOY    = 'o2auws,ogeg,ogws,ogwf,o2abin,ogdrools,ogsearch,o2adb,ogopui,o2acms'
    ENVIRONMENT_NAME = ''
    FOLDER_NAME      = ''
    BuildNumber      = ''
    BuildNummm       = ''
}
$script:LogoPath         = Join-Path $script:Root 'app-logo.png'
$script:ActiveBackendProcess = $null
$script:ForceCloseRequested = $false
$script:DeployBerlinTimeZone = $null

function Get-DeployBerlinTimeZone {
    if (-not $script:DeployBerlinTimeZone) {
        $script:DeployBerlinTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById('W. Europe Standard Time')
    }
    return $script:DeployBerlinTimeZone
}

function Get-DeployBerlinNow {
    return [TimeZoneInfo]::ConvertTime([DateTime]::UtcNow, (Get-DeployBerlinTimeZone))
}

function Get-DeployBerlinTimeAbbrev {
    param([DateTime]$When)
    $tz = Get-DeployBerlinTimeZone
    if ($tz.IsDaylightSavingTime($When)) { return 'CEST' }
    return 'CET'
}

function New-DeployBerlinScheduleTime {
    param(
        [Parameter(Mandatory = $true)][DateTime]$Date,
        [Parameter(Mandatory = $true)][int]$Hour24,
        [Parameter(Mandatory = $true)][int]$Minute
    )
    $tz = Get-DeployBerlinTimeZone
    $local = [DateTime]::new($Date.Year, $Date.Month, $Date.Day, $Hour24, $Minute, 0, [DateTimeKind]::Unspecified)
    $offset = $tz.GetUtcOffset($local)
    return [DateTimeOffset]::new($local, $offset)
}

function Format-DeployBerlinScheduleLabel {
    param([DateTimeOffset]$When)
    $abbrev = Get-DeployBerlinTimeAbbrev $When.DateTime
    return "$($When.ToString('dd-MM-yyyy HH:mm')) $abbrev"
}

function Get-StoredCitrixId {
    if (-not (Test-Path -LiteralPath $script:CitrixIdFile)) { return '' }
    try {
        $id = ([System.IO.File]::ReadAllText($script:CitrixIdFile)).Trim()
        if ($id) { return $id }
    } catch {}
    return ''
}

function Set-StoredCitrixId {
    param([string]$Id)
    $trimmed = $Id.Trim()
    if (-not $trimmed) { return }
    try {
        [System.IO.File]::WriteAllText($script:CitrixIdFile, $trimmed)
        Write-Vfd2Log "Citrix Id saved for future sign-in popups"
    } catch {
        Write-Vfd2Log "WARNING: Could not save Citrix Id: $($_.Exception.Message)"
    }
}

function Get-StoredPuttyCredentials {
    if (-not (Test-Path -LiteralPath $script:PuttyCredFile)) {
        return @{ user = ''; password = '' }
    }
    try {
        $data = Get-Content -LiteralPath $script:PuttyCredFile -Raw | ConvertFrom-Json
        return @{
            user     = [string]$data.putty_user
            password = [string]$data.putty_password
        }
    } catch {
        return @{ user = ''; password = '' }
    }
}

function Set-StoredPuttyCredentials {
    param([string]$User, [string]$Password)
    $payload = @{
        putty_user     = $User.Trim()
        putty_password = $Password
    } | ConvertTo-Json -Compress
    try {
        [System.IO.File]::WriteAllText($script:PuttyCredFile, $payload, [System.Text.UTF8Encoding]::new($false))
        Write-Vfd2Log "PuTTY credentials saved for future SIT switching"
    } catch {
        Write-Vfd2Log "WARNING: Could not save PuTTY credentials: $($_.Exception.Message)"
    }
}

function Get-StoredJenkinsUploadCredentials {
    if (-not (Test-Path -LiteralPath $script:JenkinsUploadCredFile)) {
        return @{
            jenkins_user     = ''
            jenkins_password = ''
            project_name     = ''
            release_name     = ''
            build_number     = ''
            folder_name      = ''
        }
    }
    try {
        $data = Get-Content -LiteralPath $script:JenkinsUploadCredFile -Raw | ConvertFrom-Json
        return @{
            jenkins_user     = [string]$data.jenkins_user
            jenkins_password = ''
            project_name     = [string]$data.project_name
            release_name     = [string]$data.release_name
            build_number     = [string]$data.build_number
            folder_name      = [string]$data.folder_name
        }
    } catch {
        return @{
            jenkins_user     = ''
            jenkins_password = ''
            project_name     = ''
            release_name     = ''
            build_number     = ''
            folder_name      = ''
        }
    }
}

function Set-StoredJenkinsUploadCredentials {
    param(
        [string]$JenkinsUser,
        [string]$ProjectName,
        [string]$ReleaseName,
        [string]$BuildNumber,
        [string]$FolderName
    )
    $payload = @{
        jenkins_user = $JenkinsUser.Trim()
        project_name = $ProjectName.Trim()
        release_name = $ReleaseName.Trim()
        build_number = $BuildNumber.Trim()
        folder_name  = $FolderName.Trim()
    } | ConvertTo-Json -Compress
    try {
        [System.IO.File]::WriteAllText(
            $script:JenkinsUploadCredFile,
            $payload,
            [System.Text.UTF8Encoding]::new($false))
        Write-Vfd2Log 'Jenkins upload values saved (password not stored)'
    } catch {
        Write-Vfd2Log "WARNING: Could not save Jenkins upload values: $($_.Exception.Message)"
    }
}

function Write-Vfd2Log {
    param([string]$Message)
    $entry = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff')] [GUI] [session=$($script:SessionId)] $Message"
    try {
        [System.IO.File]::AppendAllText($script:LogFile, "$entry`r`n")
    } catch {
        try { Write-Host $entry } catch {}
    }
}

Write-Vfd2Log '===== EnvPilot GUI started ====='
Write-Vfd2Log "Log file (all activity): $($script:LogFile)"

$loadedCitrixId = Get-StoredCitrixId
if ($loadedCitrixId) {
    $script:CitrixId = $loadedCitrixId
    Write-Vfd2Log "Loaded stored Citrix Id as default: $loadedCitrixId"
}

# ── Theme (formal, eye-soothing) ─────────────────────────────────────────────
$script:ColorBg         = [System.Drawing.Color]::FromArgb(245, 248, 252)
$script:ColorPanel      = [System.Drawing.Color]::FromArgb(255, 255, 255)
$script:ColorAccent     = [System.Drawing.Color]::FromArgb(0, 102, 153)
$script:ColorAccentHover = [System.Drawing.Color]::FromArgb(0, 82, 128)
$script:ColorText       = [System.Drawing.Color]::FromArgb(45, 55, 72)
$script:ColorMuted      = [System.Drawing.Color]::FromArgb(90, 100, 115)
$script:ColorBorder     = [System.Drawing.Color]::FromArgb(210, 218, 228)
$script:FontTitle       = New-Object System.Drawing.Font('Segoe UI', 12, [System.Drawing.FontStyle]::Regular)
$script:FontBody        = New-Object System.Drawing.Font('Segoe UI', 10)
$script:FontBold        = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$script:FontSmall       = New-Object System.Drawing.Font('Segoe UI', 9.5)

function Find-PythonExecutable {
    $venvPython = Join-Path $script:Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        Write-Vfd2Log "Using project venv Python: $venvPython"
        return $venvPython
    }

    # Resolve real interpreter via py launcher (avoids WindowsApps store stub)
    try {
        $pyCmd = Get-Command py.exe -ErrorAction Stop
        $resolved = (& $pyCmd.Source -3 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
        if ($resolved -and (Test-Path -LiteralPath $resolved)) {
            Write-Vfd2Log "Resolved Python via py launcher: $resolved"
            return $resolved
        }
    } catch {}

    # Search standard install locations
    $searchRoots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )
    foreach ($root in $searchRoots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        $found = Get-ChildItem -Path $root -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch 'WindowsApps' } |
            Sort-Object { $_.FullName } -Descending |
            Select-Object -First 1
        if ($found) {
            Write-Vfd2Log "Found Python install: $($found.FullName)"
            return $found.FullName
        }
    }

    # Last resort: PATH python, but skip Microsoft Store alias
    try {
        $cmd = Get-Command python.exe -ErrorAction Stop
        if ($cmd.Source -and $cmd.Source -notmatch 'WindowsApps') {
            Write-Vfd2Log "Using PATH python: $($cmd.Source)"
            return $cmd.Source
        }
    } catch {}

    return $null
}

function Update-BackendProgressStatus {
    param([string]$Action)
    if (-not (Test-Path -LiteralPath $script:LogFile)) { return }
    try {
        $lines = Get-Content -LiteralPath $script:LogFile -Tail 50 -ErrorAction SilentlyContinue
        $lastStep = ($lines | Where-Object { $_ -match '\| INFO\s+\|' -or $_ -match '\| WARNING\s+\|' } | Select-Object -Last 1)
        if ($lastStep -match '\] (.+)$') {
            $step = $Matches[1].Trim()
            if ($step.Length -gt 72) { $step = $step.Substring(0, 69) + '...' }
            Set-Status "$Action`: $step"
        }
    } catch {}
}

function Invoke-Vfd2BackendSync {
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [int]$Choice = 0,
        [string]$CitrixId = '',
        [string]$CitrixPassword = '',
        [string]$PuttyUser = '',
        [string]$PuttyPassword = '',
        [string]$SitTarget = '',
        [string]$JenkinsUser = '',
        [string]$JenkinsPassword = '',
        [string]$ProjectName = '',
        [string]$ReleaseName = '',
        [string]$BuildNumber = '',
        [string]$FolderName = '',
        [switch]$SkipDeploy
    )

    if (-not $script:PythonExe) {
        $script:PythonExe = Find-PythonExecutable
    }
    Write-Vfd2Log "Backend call: action=$Action choice=$Choice python=$($script:PythonExe)"

    if (-not $script:PythonExe) {
        Write-Vfd2Log 'ERROR: Python not found in PATH'
        return @{
            ok          = $false
            message     = 'Python was not found in PATH. Install Python 3 and ensure python is available.'
            action      = $Action
            duration_ms = 0
            session_log = $script:LogFile
        }
    }

    if (-not (Test-Path -LiteralPath $script:BackendPy)) {
        Write-Vfd2Log "ERROR: Backend script not found: $($script:BackendPy)"
        return @{
            ok          = $false
            message     = "Backend script not found: $($script:BackendPy)"
            action      = $Action
            duration_ms = 0
            session_log = $script:LogFile
        }
    }

    $argList = @(
        $script:BackendPy,
        '--session-id', $script:SessionId,
        '--action', $Action
    )
    if ($Action -eq 'switch') {
        $argList += @('--choice', [string]$Choice)
    }
    if ($Action -eq 'sit_connect' -and $SitTarget) {
        $argList += @('--sit-target', $SitTarget)
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $script:PythonExe
    $psi.Arguments              = ($argList | ForEach-Object {
        if ($_ -match '\s') { "`"$($_ -replace '"','\"')`"" } else { $_ }
    }) -join ' '
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $false
    $psi.CreateNoWindow         = $true
    $psi.WorkingDirectory       = $script:Root

    $psi.EnvironmentVariables['VFD2_LOG_FILE'] = $script:LogFile
    $psi.EnvironmentVariables['VFD2_SESSION_ID'] = $script:SessionId
    if ($SkipDeploy) {
        $psi.EnvironmentVariables['VFD2_OPEN_DEPLOY'] = '0'
    }

    if ($CitrixId -or $CitrixPassword -or $PuttyUser -or $PuttyPassword -or $SitTarget `
        -or $JenkinsUser -or $JenkinsPassword -or $ProjectName -or $ReleaseName `
        -or $BuildNumber -or $FolderName `
        -or ($script:DeployFormFields -and $script:DeployFormFields.Count -gt 0)) {
        $tempCred = Join-Path ([System.IO.Path]::GetTempPath()) "vfd2_$($script:SessionId).json"
        $credPayload = @{
            citrix_id        = $CitrixId
            password         = $CitrixPassword
            putty_user       = $PuttyUser
            putty_password   = $PuttyPassword
            sit_target       = $SitTarget
            jenkins_user     = $JenkinsUser
            jenkins_password = $JenkinsPassword
            project_name     = $ProjectName
            release_name     = $ReleaseName
            build_number     = $BuildNumber
            folder_name      = $FolderName
        }
        if ($script:DeployFormFields -and $script:DeployFormFields.Count -gt 0) {
            $credPayload['deploy_form'] = $script:DeployFormFields
            $credPayload['deploy_timing'] = [string]$script:DeployTimingMode
            if ($script:ScheduledDeployTime) {
                $credPayload['deploy_schedule'] = $script:ScheduledDeployTime.ToString('o')
            }
            Write-Vfd2Log "Deploy form fields attached for Citrix GUI automation ($($script:DeployFormFields.Count) fields)"
        }
        $credJson = $credPayload | ConvertTo-Json -Compress -Depth 5
        [System.IO.File]::WriteAllText($tempCred, $credJson, [System.Text.UTF8Encoding]::new($false))
        $psi.EnvironmentVariables['VFD2_CRED_FILE'] = $tempCred
        Write-Vfd2Log "Credentials file prepared for backend (action=$Action jenkins_user=$JenkinsUser project=$ProjectName)"
    }
    if ($CitrixId) {
        $psi.EnvironmentVariables['VFD2_CITRIX_ID'] = $CitrixId
    }
    if ($CitrixPassword) {
        $psi.EnvironmentVariables['VFD2_CITRIX_PASSWORD'] = $CitrixPassword
    }

    $proc = [System.Diagnostics.Process]::Start($psi)
    $script:ActiveBackendProcess = $proc
    $lastProgress = Get-Date

    while (-not $proc.HasExited) {
        [System.Windows.Forms.Application]::DoEvents()
        if ($script:ForceCloseRequested) {
            Write-Vfd2Log 'Force close requested - stopping backend process'
            try { $proc.Kill() } catch {}
            break
        }
        if (((Get-Date) - $lastProgress).TotalSeconds -ge 2) {
            Update-BackendProgressStatus -Action $Action
            $lastProgress = Get-Date
        }
        Start-Sleep -Milliseconds 200
    }

    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = ''
    try { $proc.WaitForExit() } catch {}
    $script:ActiveBackendProcess = $null

    if ($stdout.Trim()) {
        foreach ($line in ($stdout.Trim() -split "`n")) {
            if ($line.Trim() -and -not $line.Trim().StartsWith('{')) {
                Write-Vfd2Log "Backend stdout: $($line.Trim())"
            }
        }
    }
    if ($stderr.Trim()) {
        foreach ($line in ($stderr.Trim() -split "`n")) {
            if ($line.Trim()) {
                Write-Vfd2Log "Backend stderr: $($line.Trim())"
            }
        }
    }

    try {
        $tempCredPath = $psi.EnvironmentVariables['VFD2_CRED_FILE']
        if ($tempCredPath -and (Test-Path -LiteralPath $tempCredPath)) {
            Remove-Item -LiteralPath $tempCredPath -Force -ErrorAction SilentlyContinue
            Write-Vfd2Log 'Temporary credentials file removed'
        }
    } catch {}

    $jsonLine = ($stdout -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if ($jsonLine) {
        try {
            $parsed = $jsonLine.Trim() | ConvertFrom-Json
            if (-not $parsed.session_log) { $parsed | Add-Member -NotePropertyName session_log -NotePropertyValue $script:LogFile -Force }
            $status = if ($parsed.ok) { 'OK' } else { 'FAILED' }
            Write-Vfd2Log "Backend result: $status action=$($parsed.action) duration_ms=$($parsed.duration_ms) message=$($parsed.message)"
            return $parsed
        } catch {
            Write-Vfd2Log "ERROR: Failed to parse backend JSON: $($_.Exception.Message)"
        }
    }

    $errMsg = if ($stderr.Trim()) { $stderr.Trim() } else { "Backend failed (exit $($proc.ExitCode)). Output: $stdout" }
    Write-Vfd2Log "ERROR: $errMsg"
    return @{
        ok          = $false
        message     = $errMsg
        action      = $Action
        duration_ms = 0
        session_log = $script:LogFile
    }
}

function Start-Vfd2BackendAction {
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [int]$Choice = 0,
        [string]$CitrixId = '',
        [string]$CitrixPassword = '',
        [string]$PuttyUser = '',
        [string]$PuttyPassword = '',
        [string]$SitTarget = '',
        [string]$JenkinsUser = '',
        [string]$JenkinsPassword = '',
        [string]$ProjectName = '',
        [string]$ReleaseName = '',
        [string]$BuildNumber = '',
        [string]$FolderName = '',
        [string]$BusyText = 'Processing...',
        [string]$SuccessStatus = 'Done',
        [string]$FailStatus = 'Failed'
    )

    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        Write-Vfd2Log "Backend action '$Action' ignored: another action is still running"
        [System.Windows.Forms.MessageBox]::Show(
            'Please wait for the current action to finish.',
            'Busy', 'OK', 'Warning') | Out-Null
        return
    }

    Write-Vfd2Log "Starting backend action: $Action"
    Invoke-WithBusy -BusyText $BusyText -ActionBlock {
        $result = Invoke-Vfd2BackendSync `
            -Action $Action `
            -Choice $Choice `
            -CitrixId $CitrixId `
            -CitrixPassword $CitrixPassword `
            -PuttyUser $PuttyUser `
            -PuttyPassword $PuttyPassword `
            -SitTarget $SitTarget `
            -JenkinsUser $JenkinsUser `
            -JenkinsPassword $JenkinsPassword `
            -ProjectName $ProjectName `
            -ReleaseName $ReleaseName `
            -BuildNumber $BuildNumber `
            -FolderName $FolderName
        $statusText = if ($result.ok) { $SuccessStatus } else { $FailStatus }
        if ($result.message -and -not $result.ok) {
            $short = [string]$result.message
            if ($short.Length -gt 85) { $short = $short.Substring(0, 82) + '...' }
            $statusText = $short
        }
        Set-Status $statusText
        if (-not $result.quiet_ui) {
            Show-BackendResult $result
        } else {
            Write-Vfd2Log "Quiet UI: $($result.message)"
        }
    }
}

function Set-AppBranding {
    param([System.Windows.Forms.Panel]$Panel, [ref]$WelcomeLabel, [ref]$StartY)

    if (-not (Test-Path -LiteralPath $script:LogoPath)) {
        Write-Vfd2Log "Logo file not found: $($script:LogoPath)"
        return
    }

    try {
        $img = [System.Drawing.Image]::FromFile($script:LogoPath)
        $picLogo = New-Object System.Windows.Forms.PictureBox
        $picLogo.Image = $img
        $picLogo.SizeMode = [System.Windows.Forms.PictureBoxSizeMode]::Zoom
        $picLogo.Location = New-Object System.Drawing.Point(20, 16)
        $picLogo.Size = New-Object System.Drawing.Size(52, 52)
        $Panel.Controls.Add($picLogo)

        $WelcomeLabel.Value.Location = New-Object System.Drawing.Point(82, 18)
        $WelcomeLabel.Value.Size = New-Object System.Drawing.Size(458, 52)

        $bmp = New-Object System.Drawing.Bitmap($script:LogoPath)
        $hIcon = $bmp.GetHicon()
        $script:Form.Icon = [System.Drawing.Icon]::FromHandle($hIcon)
        Write-Vfd2Log "Application logo loaded"
    } catch {
        Write-Vfd2Log "WARNING: Could not load application logo: $($_.Exception.Message)"
    }
}

function New-StyledButton {
    param(
        [string]$Text,
        [int]$X,
        [int]$Y,
        [int]$W = 110,
        [int]$H = 34,
        [switch]$Primary
    )
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text      = $Text
    $btn.Font      = $script:FontBold
    $btn.Size      = New-Object System.Drawing.Size($W, $H)
    $btn.Location  = New-Object System.Drawing.Point($X, $Y)
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.Cursor    = [System.Windows.Forms.Cursors]::Hand
    $btn.FlatAppearance.BorderSize = 1
    if ($Primary) {
        $btn.BackColor = $script:ColorAccent
        $btn.ForeColor = [System.Drawing.Color]::White
        $btn.FlatAppearance.BorderColor = $script:ColorAccent
    } else {
        $btn.BackColor = [System.Drawing.Color]::White
        $btn.ForeColor = $script:ColorText
        $btn.FlatAppearance.BorderColor = $script:ColorBorder
    }
    return $btn
}

function Set-Status {
    param([string]$Text)
    $script:StatusLabel.Text = $Text
    [System.Windows.Forms.Application]::DoEvents()
}

function Show-BackendResult {
    param($Result)
    $title = if ($Result.ok) { 'Success' } else { 'Error' }
    $icon  = if ($Result.ok) { 'Information' } else { 'Error' }
    $msg   = $Result.message
    if ($Result.duration_ms) {
        $msg += "`n`nDuration: $($Result.duration_ms) ms"
    }
    if ($Result.session_log) {
        $msg += "`nLog: $($Result.session_log)"
    }
    try {
        $script:Form.TopMost = $true
        $script:Form.Activate()
        [System.Windows.Forms.Application]::DoEvents()
    } catch {}
    [System.Windows.Forms.MessageBox]::Show($msg, $title, 'OK', $icon) | Out-Null
    try {
        $script:Form.TopMost = $false
        $script:Form.Activate()
    } catch {}
}

function Show-CitrixSignInPopup {
    if (-not $script:CitrixId) {
        $storedId = Get-StoredCitrixId
        if ($storedId) { $script:CitrixId = $storedId }
    }

    $popup = New-Object System.Windows.Forms.Form
    $popup.Text            = 'Citrix Sign In'
    $popup.Size              = New-Object System.Drawing.Size(420, 260)
    $popup.StartPosition     = [System.Windows.Forms.FormStartPosition]::CenterParent
    $popup.FormBorderStyle   = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $popup.MaximizeBox       = $false
    $popup.MinimizeBox       = $false
    $popup.BackColor         = $script:ColorBg
    $popup.Font              = $script:FontBody

    $lblTitle = New-Object System.Windows.Forms.Label
    $lblTitle.Text      = 'Provide Citrix Id and Password'
    $lblTitle.Font      = $script:FontBold
    $lblTitle.ForeColor = $script:ColorText
    $lblTitle.Location  = New-Object System.Drawing.Point(24, 20)
    $lblTitle.Size      = New-Object System.Drawing.Size(360, 24)
    $popup.Controls.Add($lblTitle)

    $lblId = New-Object System.Windows.Forms.Label
    $lblId.Text     = 'Citrix Id:'
    $lblId.Location = New-Object System.Drawing.Point(24, 58)
    $lblId.Size     = New-Object System.Drawing.Size(90, 24)
    $popup.Controls.Add($lblId)

    $txtId = New-Object System.Windows.Forms.TextBox
    $txtId.Location    = New-Object System.Drawing.Point(120, 56)
    $txtId.Size        = New-Object System.Drawing.Size(260, 28)
    $txtId.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $txtId.ReadOnly      = $false
    if ($script:CitrixId) { $txtId.Text = $script:CitrixId }
    $popup.Controls.Add($txtId)

    $lblPass = New-Object System.Windows.Forms.Label
    $lblPass.Text     = 'Password:'
    $lblPass.Location = New-Object System.Drawing.Point(24, 98)
    $lblPass.Size     = New-Object System.Drawing.Size(90, 24)
    $popup.Controls.Add($lblPass)

    $txtPass = New-Object System.Windows.Forms.TextBox
    $txtPass.Location               = New-Object System.Drawing.Point(120, 96)
    $txtPass.Size                   = New-Object System.Drawing.Size(260, 28)
    $txtPass.BorderStyle            = [System.Windows.Forms.BorderStyle]::FixedSingle
    $txtPass.UseSystemPasswordChar  = $true
    if ($script:CitrixPassword) { $txtPass.Text = $script:CitrixPassword }
    $popup.Controls.Add($txtPass)

    $btnSignIn = New-StyledButton -Text 'Sign In' -X 120 -Y 148 -W 120 -Primary
    $btnCancel = New-StyledButton -Text 'Cancel' -X 260 -Y 148 -W 120
    $popup.Controls.Add($btnSignIn)
    $popup.Controls.Add($btnCancel)
    $popup.AcceptButton = $btnSignIn
    $popup.CancelButton = $btnCancel

    $btnSignIn.Add_Click({
        if ([string]::IsNullOrWhiteSpace($txtId.Text)) {
            [System.Windows.Forms.MessageBox]::Show('Please enter Citrix Id.', 'Validation', 'OK', 'Warning') | Out-Null
            return
        }
        if ([string]::IsNullOrWhiteSpace($txtPass.Text)) {
            [System.Windows.Forms.MessageBox]::Show('Please enter Password.', 'Validation', 'OK', 'Warning') | Out-Null
            return
        }
        $script:CitrixId = $txtId.Text.Trim()
        $script:CitrixPassword = $txtPass.Text
        Set-StoredCitrixId -Id $script:CitrixId
        Write-Vfd2Log "Citrix credentials stored for session (id=$($script:CitrixId), password_len=$($script:CitrixPassword.Length))"
        Write-Vfd2Log 'Popup password saved - will be pasted into browser only after Next on password page'
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $popup.Close()
    })

    $btnCancel.Add_Click({
        Write-Vfd2Log 'Citrix sign-in popup cancelled'
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $popup.Close()
    })

    $txtPass.Add_KeyDown({
        param($sender, $e)
        if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
            $e.SuppressKeyPress = $true
            $btnSignIn.PerformClick()
        }
    })

    return $popup.ShowDialog($script:Form)
}

function Get-LastDeployFormValues {
    if (-not (Test-Path -LiteralPath $script:DeployCsvPath)) { return $null }
    try {
        $rows = Import-Csv -LiteralPath $script:DeployCsvPath -Encoding UTF8
        if ($rows -and $rows.Count -gt 0) { return $rows[-1] }
    } catch {}
    return $null
}

function Save-DeployFormToCsv {
    param([hashtable]$Fields)
    $row = [ordered]@{}
    foreach ($key in $script:DeployFieldLabels.Keys) {
        $row[$key] = [string]$Fields[$key]
    }
    $row['Timestamp'] = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    ([PSCustomObject]$row) | Export-Csv -LiteralPath $script:DeployCsvPath -NoTypeInformation -Encoding UTF8 -Append
}

function Sync-DeployFieldsToJenkinsUpload {
    param([hashtable]$Fields)
    $script:JenkinsUploadValues = @{
        jenkins_user     = [string]$Fields.Username
        jenkins_password = [string]$Fields.Password
        project_name     = [string]$Fields.ProjectName
        release_name     = [string]$Fields.Release_name
        build_number     = [string]$Fields.BuildNumber
        folder_name      = [string]$Fields.FOLDER_NAME
    }
    Set-StoredJenkinsUploadCredentials `
        -JenkinsUser $script:JenkinsUploadValues.jenkins_user `
        -ProjectName $script:JenkinsUploadValues.project_name `
        -ReleaseName $script:JenkinsUploadValues.release_name `
        -BuildNumber $script:JenkinsUploadValues.build_number `
        -FolderName $script:JenkinsUploadValues.folder_name
}

function Show-DeployBuildFormPopup {
    param([string]$ActionTitle = 'Deploy')

    $last = Get-LastDeployFormValues
    $popup = New-Object System.Windows.Forms.Form
    $popup.Text              = 'Deployment - deploy from nexus maven 2'
    $popup.StartPosition       = [System.Windows.Forms.FormStartPosition]::CenterParent
    $popup.FormBorderStyle     = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $popup.MaximizeBox         = $false
    $popup.MinimizeBox         = $false
    $popup.AutoScroll          = $false
    $popup.BackColor           = [System.Drawing.Color]::LightBlue
    $popup.Font                = $script:FontBody

    $labelFont = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $fieldFont = New-Object System.Drawing.Font('Segoe UI', 10)
    $labelWidth = 160
    $fieldLeft  = 180
    $fieldWidth = 410
    $rowHeight  = 38
    $yPos       = 12
    $textBoxes  = @{}

    foreach ($key in $script:DeployFieldLabels.Keys) {
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text      = $script:DeployFieldLabels[$key]
        $lbl.Font      = $labelFont
        $lbl.ForeColor = [System.Drawing.Color]::FromArgb(60, 60, 60)
        $lbl.Size      = New-Object System.Drawing.Size($labelWidth, 26)
        $lbl.Location  = New-Object System.Drawing.Point(12, ($yPos + 4))
        $lbl.TextAlign = [System.Drawing.ContentAlignment]::MiddleRight
        $popup.Controls.Add($lbl)

        $txt = New-Object System.Windows.Forms.TextBox
        $txt.Font        = $fieldFont
        $txt.Location    = New-Object System.Drawing.Point($fieldLeft, $yPos)
        $txt.Size        = New-Object System.Drawing.Size($fieldWidth, 28)
        $txt.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
        $txt.BackColor   = [System.Drawing.Color]::White
        if ($script:DeployAlwaysDefault -contains $key) {
            $txt.Text = [string]$script:DeployDefaults[$key]
        } elseif ($last -and $last.PSObject.Properties[$key]) {
            $txt.Text = [string]$last.$key
        } elseif ($script:DeployAlwaysEmpty -contains $key) {
            $txt.Text = ''
        } else {
            $txt.Text = [string]$script:DeployDefaults[$key]
        }
        if ($key -eq 'Password') { $txt.UseSystemPasswordChar = $true }
        $popup.Controls.Add($txt)
        $textBoxes[$key] = $txt
        $yPos += $rowHeight
    }

    $btnSubmit = New-Object System.Windows.Forms.Button
    $btnSubmit.Text      = 'Submit'
    $btnSubmit.Font      = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $btnSubmit.Size      = New-Object System.Drawing.Size(120, 36)
    $btnSubmit.Location  = New-Object System.Drawing.Point($fieldLeft, ($yPos + 6))
    $btnSubmit.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnSubmit.BackColor = [System.Drawing.Color]::FromArgb(0, 120, 212)
    $btnSubmit.ForeColor = [System.Drawing.Color]::White
    $btnSubmit.FlatAppearance.BorderSize = 0
    $btnSubmit.Cursor    = [System.Windows.Forms.Cursors]::Hand
    $popup.Controls.Add($btnSubmit)
    $popup.AcceptButton = $btnSubmit

    $contentBottom = $btnSubmit.Location.Y + $btnSubmit.Height + 14
    $contentWidth  = $fieldLeft + $fieldWidth + 16
    $popup.ClientSize = New-Object System.Drawing.Size($contentWidth, $contentBottom)

    $btnSubmit.Add_Click({
        if ([string]::IsNullOrWhiteSpace($textBoxes['Username'].Text) -or
            [string]::IsNullOrWhiteSpace($textBoxes['Password'].Text)) {
            [System.Windows.Forms.MessageBox]::Show(
                'Please enter Username and Password.',
                'Missing credentials', 'OK', 'Warning') | Out-Null
            return
        }
        $fields = @{}
        foreach ($key in $script:DeployFieldLabels.Keys) {
            $fields[$key] = $textBoxes[$key].Text
        }
        $script:DeployFormFields = $fields
        Write-Vfd2Log "Deploy form submitted ($ActionTitle)"
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $popup.Close()
    })

    return $popup.ShowDialog($script:Form)
}

function Show-DeployWhenPopup {
    $script:ScheduledDeployTime = $null
    $berlinNow = Get-DeployBerlinNow
    $berlinTzAbbrev = Get-DeployBerlinTimeAbbrev $berlinNow

    $popup = New-Object System.Windows.Forms.Form
    $popup.Text              = 'When to deploy the build?'
    $popup.StartPosition       = [System.Windows.Forms.FormStartPosition]::CenterParent
    $popup.FormBorderStyle     = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $popup.MaximizeBox         = $false
    $popup.MinimizeBox         = $false
    $popup.AutoScroll          = $false
    $popup.BackColor           = [System.Drawing.Color]::LightBlue
    $popup.Font                = $script:FontBody

    $lblQ = New-Object System.Windows.Forms.Label
    $lblQ.Text     = 'When do you want to deploy the build?'
    $lblQ.Font     = New-Object System.Drawing.Font('Segoe UI', 12, [System.Drawing.FontStyle]::Bold)
    $lblQ.Size     = New-Object System.Drawing.Size(380, 30)
    $lblQ.Location = New-Object System.Drawing.Point(15, 16)
    $lblQ.BackColor = [System.Drawing.Color]::LightBlue
    $popup.Controls.Add($lblQ)

    $btnNow = New-Object System.Windows.Forms.Button
    $btnNow.Text      = 'Deploy Now'
    $btnNow.Font      = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $btnNow.Size      = New-Object System.Drawing.Size(160, 40)
    $btnNow.Location  = New-Object System.Drawing.Point(30, 58)
    $btnNow.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnNow.BackColor = [System.Drawing.Color]::FromArgb(0, 120, 212)
    $btnNow.ForeColor = [System.Drawing.Color]::White
    $btnNow.FlatAppearance.BorderSize = 0
    $btnNow.Cursor    = [System.Windows.Forms.Cursors]::Hand
    $popup.Controls.Add($btnNow)

    $btnLater = New-Object System.Windows.Forms.Button
    $btnLater.Text      = 'Deploy Later'
    $btnLater.Font      = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $btnLater.Size      = New-Object System.Drawing.Size(160, 40)
    $btnLater.Location  = New-Object System.Drawing.Point(220, 58)
    $btnLater.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnLater.BackColor = [System.Drawing.Color]::FromArgb(76, 175, 80)
    $btnLater.ForeColor = [System.Drawing.Color]::White
    $btnLater.FlatAppearance.BorderSize = 0
    $btnLater.Cursor    = [System.Windows.Forms.Cursors]::Hand
    $popup.Controls.Add($btnLater)

    $pnlSchedule = New-Object System.Windows.Forms.Panel
    $pnlSchedule.Location = New-Object System.Drawing.Point(15, 110)
    $pnlSchedule.Size     = New-Object System.Drawing.Size(380, 148)
    $pnlSchedule.BackColor = [System.Drawing.Color]::LightBlue
    $pnlSchedule.AutoScroll = $false

    $lblTzHint = New-Object System.Windows.Forms.Label
    $lblTzHint.Text     = "German time (Europe/Berlin) - currently $berlinTzAbbrev"
    $lblTzHint.Font     = New-Object System.Drawing.Font('Segoe UI', 9)
    $lblTzHint.Size     = New-Object System.Drawing.Size(380, 20)
    $lblTzHint.Location = New-Object System.Drawing.Point(0, 0)
    $pnlSchedule.Controls.Add($lblTzHint)

    $lblDate = New-Object System.Windows.Forms.Label
    $lblDate.Text     = 'Date:'
    $lblDate.Font     = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $lblDate.Size     = New-Object System.Drawing.Size(50, 25)
    $lblDate.Location = New-Object System.Drawing.Point(0, 28)
    $pnlSchedule.Controls.Add($lblDate)

    $dtPicker = New-Object System.Windows.Forms.DateTimePicker
    $dtPicker.Format       = [System.Windows.Forms.DateTimePickerFormat]::Custom
    $dtPicker.CustomFormat = 'dd-MM-yyyy'
    $dtPicker.Size         = New-Object System.Drawing.Size(150, 28)
    $dtPicker.Location     = New-Object System.Drawing.Point(55, 25)
    $dtPicker.Value        = $berlinNow
    $dtPicker.MinDate      = [DateTime]::new($berlinNow.Year, $berlinNow.Month, $berlinNow.Day)
    $pnlSchedule.Controls.Add($dtPicker)

    $lblTime = New-Object System.Windows.Forms.Label
    $lblTime.Text     = "Time ($berlinTzAbbrev):"
    $lblTime.Font     = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $lblTime.Size     = New-Object System.Drawing.Size(120, 25)
    $lblTime.Location = New-Object System.Drawing.Point(0, 65)
    $pnlSchedule.Controls.Add($lblTime)

    $txtTime = New-Object System.Windows.Forms.TextBox
    $txtTime.Font      = New-Object System.Drawing.Font('Segoe UI', 10)
    $txtTime.Size      = New-Object System.Drawing.Size(65, 28)
    $txtTime.Location  = New-Object System.Drawing.Point(125, 63)
    $txtTime.TextAlign = [System.Windows.Forms.HorizontalAlignment]::Center
    $nextHr = $berlinNow.AddHours(1)
    $displayHour = if ($nextHr.Hour -eq 0) { 12 } elseif ($nextHr.Hour -gt 12) { $nextHr.Hour - 12 } else { $nextHr.Hour }
    $txtTime.Text = "$($displayHour.ToString('00')):$($nextHr.Minute.ToString('00'))"
    $pnlSchedule.Controls.Add($txtTime)

    $cboAmPm = New-Object System.Windows.Forms.ComboBox
    $cboAmPm.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
    $cboAmPm.Size     = New-Object System.Drawing.Size(55, 28)
    $cboAmPm.Location = New-Object System.Drawing.Point(195, 63)
    $cboAmPm.Font     = New-Object System.Drawing.Font('Segoe UI', 10)
    $cboAmPm.Items.AddRange(@('AM', 'PM'))
    $cboAmPm.SelectedIndex = if ($nextHr.Hour -ge 12) { 1 } else { 0 }
    $pnlSchedule.Controls.Add($cboAmPm)

    $btnSchedule = New-Object System.Windows.Forms.Button
    $btnSchedule.Text      = 'Schedule Deployment'
    $btnSchedule.Font      = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $btnSchedule.Size      = New-Object System.Drawing.Size(200, 36)
    $btnSchedule.Location  = New-Object System.Drawing.Point(0, 104)
    $btnSchedule.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnSchedule.BackColor = [System.Drawing.Color]::FromArgb(0, 120, 212)
    $btnSchedule.ForeColor = [System.Drawing.Color]::White
    $btnSchedule.FlatAppearance.BorderSize = 0
    $btnSchedule.Cursor    = [System.Windows.Forms.Cursors]::Hand
    $pnlSchedule.Controls.Add($btnSchedule)

    $btnCancel = New-Object System.Windows.Forms.Button
    $btnCancel.Text      = 'Cancel'
    $btnCancel.Font      = New-Object System.Drawing.Font('Segoe UI', 10)
    $btnCancel.Size      = New-Object System.Drawing.Size(100, 34)
    $btnCancel.Location  = New-Object System.Drawing.Point(155, 268)
    $btnCancel.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnCancel.BackColor = [System.Drawing.Color]::LightBlue
    $btnCancel.ForeColor = [System.Drawing.Color]::FromArgb(60, 60, 60)
    $btnCancel.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(160, 160, 160)
    $btnCancel.FlatAppearance.BorderSize = 1
    $btnCancel.Cursor    = [System.Windows.Forms.Cursors]::Hand

    $btnNow.Add_Click({
        Write-Vfd2Log 'User chose: Deploy Now'
        $script:DeployTimingMode = 'now'
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Yes
        $popup.Close()
    })

    $btnLater.Add_Click({
        Write-Vfd2Log 'User chose: Deploy Later (schedule panel)'
        $script:DeployTimingMode = 'later'
        if ($null -eq $pnlSchedule.Parent) {
            $popup.Controls.Add($pnlSchedule)
        }
        if ($null -eq $btnCancel.Parent) {
            $popup.Controls.Add($btnCancel)
            $popup.CancelButton = $btnCancel
        }
        $popup.ClientSize = New-Object System.Drawing.Size(410, 312)
    })

    $btnSchedule.Add_Click({
        $selDate = $dtPicker.Value.Date
        $timeText = $txtTime.Text.Trim()
        $amPm = [string]$cboAmPm.SelectedItem
        $timeParts = $timeText -split ':'
        if ($timeParts.Count -ne 2) {
            [System.Windows.Forms.MessageBox]::Show(
                'Please enter time in hh:mm format (e.g. 08:29).',
                'Invalid Time', 'OK', 'Warning') | Out-Null
            return
        }
        $selHour = 0; $selMin = 0
        if (-not [int]::TryParse($timeParts[0], [ref]$selHour) -or -not [int]::TryParse($timeParts[1], [ref]$selMin)) {
            [System.Windows.Forms.MessageBox]::Show(
                'Please enter valid numbers for hour and minute.',
                'Invalid Time', 'OK', 'Warning') | Out-Null
            return
        }
        if ($selHour -lt 1 -or $selHour -gt 12 -or $selMin -lt 0 -or $selMin -gt 59) {
            [System.Windows.Forms.MessageBox]::Show(
                'Hour must be 1-12 and minute must be 0-59.',
                'Invalid Time', 'OK', 'Warning') | Out-Null
            return
        }
        if ($amPm -eq 'AM') {
            if ($selHour -eq 12) { $selHour = 0 }
        } elseif ($selHour -ne 12) {
            $selHour = $selHour + 12
        }
        $schedTime = New-DeployBerlinScheduleTime -Date $selDate -Hour24 $selHour -Minute $selMin
        if ($schedTime.UtcDateTime -le [DateTime]::UtcNow) {
            [System.Windows.Forms.MessageBox]::Show(
                "Please select a future date and time in German time ($berlinTzAbbrev).",
                'Invalid Time', 'OK', 'Warning') | Out-Null
            return
        }
        $script:ScheduledDeployTime = $schedTime
        $schedLabel = Format-DeployBerlinScheduleLabel $schedTime
        Write-Vfd2Log "Deploy scheduled for $schedLabel (Europe/Berlin)"
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Retry
        $popup.Close()
    })

    $btnCancel.Add_Click({
        $script:DeployTimingMode = ''
        $script:ScheduledDeployTime = $null
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $popup.Close()
    })

    $popup.ClientSize = New-Object System.Drawing.Size(410, 112)

    return $popup.ShowDialog($script:Form)
}

function Invoke-DeployFlow {
    Write-Vfd2Log 'Deploy flow: form -> timing -> Citrix (CASE A/B/C)'

    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show(
            'Please wait for the current action to finish.',
            'Busy', 'OK', 'Warning') | Out-Null
        return
    }

    $formResult = Show-DeployBuildFormPopup
    if ($formResult -ne [System.Windows.Forms.DialogResult]::OK) { return }
    if (-not $script:DeployFormFields -or $script:DeployFormFields.Count -eq 0) { return }

    $whenResult = Show-DeployWhenPopup
    if ($whenResult -eq [System.Windows.Forms.DialogResult]::Cancel) { return }
    if ($whenResult -eq [System.Windows.Forms.DialogResult]::Yes) {
        $script:DeployTimingMode = 'now'
    } elseif ($whenResult -eq [System.Windows.Forms.DialogResult]::Retry) {
        $script:DeployTimingMode = 'later'
        if (-not $script:ScheduledDeployTime) { return }
    } else {
        return
    }

    try { Save-DeployFormToCsv -Fields $script:DeployFormFields } catch {}
    Write-Vfd2Log 'Deploy form saved to deploy-data.csv'

    if ($script:DeployTimingMode -eq 'now') {
        Set-Status 'Deploy Now: checking Citrix session...'
        Write-Vfd2Log 'Deploy Now selected - starting Citrix login (CASE A/B/C)'
    } else {
        $schedLabel = Format-DeployBerlinScheduleLabel $script:ScheduledDeployTime
        Set-Status "Deploy Later ($schedLabel): checking Citrix..."
        Write-Vfd2Log 'Deploy Later scheduled - starting Citrix login (CASE A/B/C)'
    }

    Invoke-DeployCitrixFlow
}

function Invoke-LaunchDeployGui {
    if (-not (Test-Path -LiteralPath $script:DeployBat)) {
        [System.Windows.Forms.MessageBox]::Show(
            "deploy.bat not found:`n$($script:DeployBat)",
            'Deploy', 'OK', 'Error') | Out-Null
        return $false
    }
    Write-Vfd2Log "Launching deploy.bat GUI: $($script:DeployBat)"
    Start-Process -FilePath $script:DeployBat -WorkingDirectory $script:DeployRoot | Out-Null
    Set-Status 'Deploy: deploy.bat GUI opened'
    return $true
}

function Start-DeployBatGui {
    param([hashtable]$Fields)
    if (-not (Test-Path -LiteralPath $script:DeployBat)) {
        [System.Windows.Forms.MessageBox]::Show(
            "deploy.bat not found:`n$($script:DeployBat)",
            'Deploy', 'OK', 'Error') | Out-Null
        return $false
    }
    try { Save-DeployFormToCsv -Fields $Fields } catch {}
    Write-Vfd2Log "Launching deploy.bat GUI: $($script:DeployBat)"
    Start-Process -FilePath $script:DeployBat -WorkingDirectory $script:DeployRoot | Out-Null
    Set-Status 'Deploy Later: deploy.bat GUI opened'
    return $true
}

function Invoke-UploadFlow {
    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show(
            'Please wait for the current action to finish.',
            'Busy', 'OK', 'Warning') | Out-Null
        return
    }

    $formResult = Show-JenkinsUploadPopup
    if ($formResult -ne [System.Windows.Forms.DialogResult]::OK) { return }
    if (-not $script:JenkinsUploadValues -or $script:JenkinsUploadValues.Count -eq 0) { return }

    Start-Vfd2BackendAction -Action 'upload' `
        -JenkinsUser $script:JenkinsUploadValues.jenkins_user `
        -JenkinsPassword $script:JenkinsUploadValues.jenkins_password `
        -ProjectName $script:JenkinsUploadValues.project_name `
        -ReleaseName $script:JenkinsUploadValues.release_name `
        -BuildNumber $script:JenkinsUploadValues.build_number `
        -FolderName $script:JenkinsUploadValues.folder_name `
        -BusyText 'Jenkins upload running (browser may open)...' `
        -SuccessStatus 'Last: Upload completed' `
        -FailStatus 'Upload failed - see log'
}

function Invoke-SharedDeployGuiFlow {
    param(
        [Parameter(Mandatory = $true)][string]$ActionName
    )

    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show(
            'Please wait for the current action to finish.',
            'Busy', 'OK', 'Warning') | Out-Null
        return $null
    }

    $formResult = Show-DeployBuildFormPopup -ActionTitle $ActionName
    if ($formResult -ne [System.Windows.Forms.DialogResult]::OK) { return $null }
    if (-not $script:DeployFormFields -or $script:DeployFormFields.Count -eq 0) { return $null }

    $whenResult = Show-DeployWhenPopup
    if ($whenResult -eq [System.Windows.Forms.DialogResult]::Cancel) { return $null }

    return @{
        Mode   = $script:DeployTimingMode
        Fields = $script:DeployFormFields
    }
}

function Invoke-DeployCitrixFlow {
    $script:DesktopAlreadyOpen = $false
    $script:DesktopLocked = $false
    Invoke-WithBusy -BusyText 'Citrix CASE A: checking for open KIAS desktop...' -ActionBlock {
        $focusResult = Invoke-Vfd2BackendSync -Action 'focus_desktop'
        if ($focusResult.ok) {
            $script:DesktopAlreadyOpen = $true
            Set-Status 'Deploy: CASE A - KIAS desktop open (70% view)'
            Write-Vfd2Log "Citrix CASE A: existing desktop - $($focusResult.message)"
        } elseif ($focusResult.locked) {
            $script:DesktopLocked = $true
            Write-Vfd2Log 'Citrix CASE B: KIAS desktop is locked - will prompt for password'
        } else {
            Write-Vfd2Log 'Citrix CASE C: no KIAS desktop - full Citrix sign-in required'
        }
    }

    if ($script:DesktopAlreadyOpen) { return }

    if ($script:DesktopLocked) {
        $dialogResult = Show-CitrixSignInPopup
        if ($dialogResult -ne [System.Windows.Forms.DialogResult]::OK) { return }

        Invoke-WithBusy -BusyText 'Citrix CASE B: unlocking desktop...' -ActionBlock {
            $unlockResult = Invoke-Vfd2BackendSync -Action 'focus_desktop' `
                -CitrixPassword $script:CitrixPassword
            if ($unlockResult.ok) {
                Set-Status 'Deploy: CASE B - unlocked on Citrix desktop (70% view)'
                Write-Vfd2Log "Citrix CASE B unlocked: $($unlockResult.message)"
            } else {
                Set-Status 'Citrix unlock failed - see log'
                Show-BackendResult $unlockResult
            }
        }
        return
    }

    $dialogResult = Show-CitrixSignInPopup
    if ($dialogResult -ne [System.Windows.Forms.DialogResult]::OK) { return }

    Write-Vfd2Log 'Citrix CASE C: starting full sign-in and deploy on KIAS desktop'
    Start-Vfd2BackendAction -Action 'deploy' `
        -CitrixId $script:CitrixId `
        -CitrixPassword $script:CitrixPassword `
        -BusyText 'Deploy running (see status below)...' `
        -SuccessStatus 'Last: Deploy completed' `
        -FailStatus 'Deploy failed - see log'
}

function Show-JenkinsUploadPopup {
    if (-not (Test-Path -LiteralPath $script:JenkinsUploadDir)) {
        [System.Windows.Forms.MessageBox]::Show(
            "Jenkins Deployment Script folder not found:`n$($script:JenkinsUploadDir)",
            'Upload', 'OK', 'Error') | Out-Null
        return [System.Windows.Forms.DialogResult]::Cancel
    }

    $stored = Get-StoredJenkinsUploadCredentials

    $popup = New-Object System.Windows.Forms.Form
    $popup.Text            = 'Jenkins Upload'
    $popup.Size              = New-Object System.Drawing.Size(480, 460)
    $popup.StartPosition     = [System.Windows.Forms.FormStartPosition]::CenterParent
    $popup.FormBorderStyle   = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $popup.MaximizeBox       = $false
    $popup.MinimizeBox       = $false
    $popup.BackColor         = $script:ColorBg
    $popup.Font              = $script:FontBody

    $lblTitle = New-Object System.Windows.Forms.Label
    $lblTitle.Text      = 'Upload build to Jenkins (upload_zip_to_vfde_nexus)'
    $lblTitle.Font      = $script:FontBold
    $lblTitle.ForeColor = $script:ColorText
    $lblTitle.Location  = New-Object System.Drawing.Point(24, 16)
    $lblTitle.Size      = New-Object System.Drawing.Size(420, 40)
    $popup.Controls.Add($lblTitle)

    $fields = @(
        @{ Key = 'jenkins_user'; Label = 'Jenkins username:'; Hint = ''; Y = 62; Secret = $false; Combo = $false },
        @{ Key = 'jenkins_password'; Label = 'Jenkins password:'; Hint = ''; Y = 98; Secret = $true; Combo = $false },
        @{ Key = 'project_name'; Label = 'ProjectName:'; Hint = 'OGW / O2A / SKY'; Y = 134; Secret = $false; Combo = $true },
        @{ Key = 'release_name'; Label = 'Release_name:'; Hint = 'e.g. 4000_WAVE11'; Y = 188; Secret = $false; Combo = $false },
        @{ Key = 'build_number'; Label = 'BuildNumber:'; Hint = 'Must match Jenkins dropdown'; Y = 248; Secret = $false; Combo = $false },
        @{ Key = 'folder_name'; Label = 'Folder_Name:'; Hint = 'e.g. 26.06.OMA'; Y = 308; Secret = $false; Combo = $false }
    )

    $textBoxes = @{}
    foreach ($field in $fields) {
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text     = $field.Label
        $lbl.Location = New-Object System.Drawing.Point(24, $field.Y)
        $lbl.Size     = New-Object System.Drawing.Size(150, 22)
        $lbl.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
        $popup.Controls.Add($lbl)

        if ($field.Combo) {
            $input = New-Object System.Windows.Forms.ComboBox
            $input.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
            $input.Items.AddRange(@('OGW', 'O2A', 'SKY'))
            $input.Location = New-Object System.Drawing.Point(180, ($field.Y - 2))
            $input.Size     = New-Object System.Drawing.Size(260, 28)
            if ($stored.ContainsKey($field.Key) -and $stored[$field.Key]) {
                $idx = $input.Items.IndexOf([string]$stored[$field.Key])
                if ($idx -ge 0) { $input.SelectedIndex = $idx } else { $input.SelectedIndex = 0 }
            } else {
                $input.SelectedIndex = 0
            }
        } else {
            $input = New-Object System.Windows.Forms.TextBox
            $input.Location    = New-Object System.Drawing.Point(180, ($field.Y - 2))
            $input.Size        = New-Object System.Drawing.Size(260, 28)
            $input.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
            if ($field.Secret) {
                $input.UseSystemPasswordChar = $true
            }
            if ($stored.ContainsKey($field.Key) -and $stored[$field.Key]) {
                $input.Text = $stored[$field.Key]
            }
        }
        $popup.Controls.Add($input)
        $textBoxes[$field.Key] = $input

        if ($field.Hint) {
            $hint = New-Object System.Windows.Forms.Label
            $hint.Text      = $field.Hint
            $hint.Font      = $script:FontSmall
            $hint.ForeColor = $script:ColorMuted
            $hint.Location  = New-Object System.Drawing.Point(180, ($field.Y + 28))
            $hint.Size      = New-Object System.Drawing.Size(260, 18)
            $popup.Controls.Add($hint)
        }
    }

    $btnUpload = New-StyledButton -Text 'Upload' -X 180 -Y 368 -W 110 -Primary
    $btnCancel = New-StyledButton -Text 'Cancel' -X 300 -Y 368 -W 110
    $popup.Controls.Add($btnUpload)
    $popup.Controls.Add($btnCancel)
    $popup.AcceptButton = $btnUpload
    $popup.CancelButton = $btnCancel

    $script:JenkinsUploadValues = @{}

    $btnUpload.Add_Click({
        $labels = @{
            jenkins_user     = 'Jenkins username'
            jenkins_password = 'Jenkins password'
            project_name     = 'ProjectName'
            release_name     = 'Release_name'
            build_number     = 'BuildNumber'
            folder_name      = 'Folder_Name'
        }
        foreach ($key in $labels.Keys) {
            $value = if ($textBoxes[$key] -is [System.Windows.Forms.ComboBox]) {
                [string]$textBoxes[$key].SelectedItem
            } else {
                [string]$textBoxes[$key].Text
            }
            if ([string]::IsNullOrWhiteSpace($value)) {
                [System.Windows.Forms.MessageBox]::Show(
                    "Please enter $($labels[$key]).",
                    'Validation', 'OK', 'Warning') | Out-Null
                return
            }
        }
        $script:JenkinsUploadValues = @{
            jenkins_user     = $textBoxes['jenkins_user'].Text.Trim()
            jenkins_password = $textBoxes['jenkins_password'].Text
            project_name     = [string]$textBoxes['project_name'].SelectedItem
            release_name     = $textBoxes['release_name'].Text.Trim()
            build_number     = $textBoxes['build_number'].Text.Trim()
            folder_name      = $textBoxes['folder_name'].Text.Trim()
        }
        Set-StoredJenkinsUploadCredentials `
            -JenkinsUser $script:JenkinsUploadValues.jenkins_user `
            -ProjectName $script:JenkinsUploadValues.project_name `
            -ReleaseName $script:JenkinsUploadValues.release_name `
            -BuildNumber $script:JenkinsUploadValues.build_number `
            -FolderName $script:JenkinsUploadValues.folder_name
        Write-Vfd2Log "Jenkins upload form submitted: project=$($script:JenkinsUploadValues.project_name) release=$($script:JenkinsUploadValues.release_name)"
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $popup.Close()
    })

    $btnCancel.Add_Click({
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $popup.Close()
    })

    return $popup.ShowDialog($script:Form)
}

function Invoke-WithBusy {
    param(
        [scriptblock]$ActionBlock,
        [string]$BusyText = 'Processing...'
    )
    $script:Form.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
    Set-Status $BusyText
    foreach ($c in $script:ActionControls) { $c.Enabled = $false }
    try {
        & $ActionBlock
    } finally {
        foreach ($c in $script:ActionControls) { $c.Enabled = $true }
        $script:Form.Cursor = [System.Windows.Forms.Cursors]::Default
    }
}

# ── Form ─────────────────────────────────────────────────────────────────────
$script:Form = New-Object System.Windows.Forms.Form
$script:Form.Text          = 'EnvPilot - VFD2 Console'
$script:Form.Size          = New-Object System.Drawing.Size(620, 520)
$script:Form.MinimumSize   = New-Object System.Drawing.Size(560, 480)
$script:Form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$script:Form.BackColor     = $script:ColorBg
$script:Form.Font          = $script:FontBody
$script:Form.Padding       = New-Object System.Windows.Forms.Padding(20)

$mainPanel = New-Object System.Windows.Forms.Panel
$mainPanel.Dock      = [System.Windows.Forms.DockStyle]::Fill
$mainPanel.BackColor = $script:ColorBg
$mainPanel.Padding   = New-Object System.Windows.Forms.Padding(8)

$content = New-Object System.Windows.Forms.Panel
$content.Location = New-Object System.Drawing.Point(12, 12)
$content.Size     = New-Object System.Drawing.Size(560, 400)
$content.BackColor = $script:ColorPanel
$content.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle

$y = 20

# Welcome header
$lblWelcome = New-Object System.Windows.Forms.Label
$lblWelcome.Text      = "Welcome to EnvPilot`r`nGuided VFD2 environment operations."
$lblWelcome.Font      = $script:FontTitle
$lblWelcome.ForeColor = $script:ColorText
$lblWelcome.Location  = New-Object System.Drawing.Point(20, $y)
$lblWelcome.Size      = New-Object System.Drawing.Size(520, 52)
$lblWelcome.AutoSize  = $false
$content.Controls.Add($lblWelcome)
Set-AppBranding -Panel $content -WelcomeLabel ([ref]$lblWelcome) -StartY ([ref]$y)
$y += 58

# Build section
$lblBuild = New-Object System.Windows.Forms.Label
$lblBuild.Text      = 'Upload or Deploy your build below:'
$lblBuild.Font      = $script:FontBold
$lblBuild.ForeColor = $script:ColorText
$lblBuild.Location  = New-Object System.Drawing.Point(20, $y)
$lblBuild.Size      = New-Object System.Drawing.Size(520, 24)
$content.Controls.Add($lblBuild)
$y += 32

$btnUpload = New-StyledButton -Text 'Upload' -X 20 -Y $y -W 120 -Primary
$btnDeploy = New-StyledButton -Text 'Deploy' -X 155 -Y $y -W 120 -Primary
$content.Controls.Add($btnUpload)
$content.Controls.Add($btnDeploy)
$y += 52

# Environment switch section
$lblSwitchTitle = New-Object System.Windows.Forms.Label
$lblSwitchTitle.Text      = 'Switch the Environment. Select below ENV that you want to switch.'
$lblSwitchTitle.Font      = $script:FontBold
$lblSwitchTitle.ForeColor = $script:ColorText
$lblSwitchTitle.Location  = New-Object System.Drawing.Point(20, $y)
$lblSwitchTitle.Size      = New-Object System.Drawing.Size(520, 24)
$content.Controls.Add($lblSwitchTitle)
$y += 30

$envLines = @(
    '1. PT to P2',
    '2. P2 to PT',
    '3. dev1b to dev2b',
    '4. dev2b to dev1b',
    '5. INT3  to INT4',
    '6.  INT4 to INT3'
)
$lblOptions = New-Object System.Windows.Forms.Label
$lblOptions.Text      = ($envLines -join "`r`n")
$lblOptions.Font      = $script:FontSmall
$lblOptions.ForeColor = $script:ColorMuted
$lblOptions.Location  = New-Object System.Drawing.Point(36, $y)
$lblOptions.Size      = New-Object System.Drawing.Size(480, 120)
$content.Controls.Add($lblOptions)
$y += 128

# Choice input + Submit
$lblChoice = New-Object System.Windows.Forms.Label
$lblChoice.Text      = 'Enter option (1-6):'
$lblChoice.Font      = $script:FontBody
$lblChoice.ForeColor = $script:ColorText
$lblChoice.Location  = New-Object System.Drawing.Point(20, $y)
$lblChoice.Size      = New-Object System.Drawing.Size(140, 28)
$lblChoice.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$content.Controls.Add($lblChoice)

$txtChoice = New-Object System.Windows.Forms.TextBox
$txtChoice.Font        = $script:FontBody
$txtChoice.Location    = New-Object System.Drawing.Point(165, ($y + 2))
$txtChoice.Size        = New-Object System.Drawing.Size(48, 28)
$txtChoice.MaxLength   = 1
$txtChoice.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$txtChoice.BackColor   = [System.Drawing.Color]::White
$txtChoice.TextAlign   = [System.Windows.Forms.HorizontalAlignment]::Center
$content.Controls.Add($txtChoice)

$btnSubmit = New-StyledButton -Text 'Submit' -X 230 -Y ($y - 2) -W 100 -Primary
$content.Controls.Add($btnSubmit)
$y += 48

$script:StatusLabel = New-Object System.Windows.Forms.Label
$script:StatusLabel.Text      = 'Ready'
$script:StatusLabel.Font      = $script:FontSmall
$script:StatusLabel.ForeColor = $script:ColorMuted
$script:StatusLabel.Location  = New-Object System.Drawing.Point(20, $y)
$script:StatusLabel.Size      = New-Object System.Drawing.Size(520, 22)
$content.Controls.Add($script:StatusLabel)

$mainPanel.Controls.Add($content)
$script:Form.Controls.Add($mainPanel)

$script:ActionControls = @($btnUpload, $btnDeploy, $btnSubmit, $txtChoice)

# Numeric-only input (1-6)
$txtChoice.Add_KeyPress({
    param($sender, $e)
    if ([char]::IsControl($e.KeyChar)) { return }
    if (-not [char]::IsDigit($e.KeyChar)) {
        $e.Handled = $true
        return
    }
    $digit = [int][string]$e.KeyChar
    if ($digit -lt 1 -or $digit -gt 6) {
        $e.Handled = $true
    }
})

function Test-SwitchChoice {
    param([string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) {
        return @{ valid = $false; message = 'Please enter a number from 1 to 6.'; choice = 0 }
    }
    if ($Raw -notmatch '^\d$') {
        return @{ valid = $false; message = 'Only numbers 1 through 6 are allowed.'; choice = 0 }
    }
    $n = [int]$Raw
    if ($n -lt 1 -or $n -gt 6) {
        return @{ valid = $false; message = 'Please enter a value from 1 to 6 only.'; choice = 0 }
    }
    return @{ valid = $true; message = ''; choice = $n }
}

function Show-SitEnvironmentPopup {
    $popup = New-Object System.Windows.Forms.Form
    $popup.Text            = 'Select SIT Environment'
    $popup.Size              = New-Object System.Drawing.Size(460, 300)
    $popup.StartPosition     = [System.Windows.Forms.FormStartPosition]::CenterParent
    $popup.FormBorderStyle   = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $popup.MaximizeBox       = $false
    $popup.MinimizeBox       = $false
    $popup.BackColor         = $script:ColorBg
    $popup.Font              = $script:FontBody

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text      = 'Which environment do you want to make changes to?'
    $lbl.Font      = $script:FontBold
    $lbl.ForeColor = $script:ColorText
    $lbl.Location  = New-Object System.Drawing.Point(24, 20)
    $lbl.Size      = New-Object System.Drawing.Size(400, 40)
    $popup.Controls.Add($lbl)

    $script:SelectedSitTarget = ''
    $buttonMap = @(
        @{ Text = 'SIT1'; Target = 'sit1'; X = 24;  Y = 72 },
        @{ Text = 'SIT2'; Target = 'sit2'; X = 150; Y = 72 },
        @{ Text = 'SIT3'; Target = 'sit3'; X = 276; Y = 72 },
        @{ Text = 'SIT4'; Target = 'sit4'; X = 24;  Y = 128 },
        @{ Text = 'SIT5'; Target = 'sit5'; X = 150; Y = 128 },
        @{ Text = 'All SIT Env'; Target = 'all_sit'; X = 276; Y = 128 }
    )

    foreach ($item in $buttonMap) {
        $btn = New-StyledButton -Text $item.Text -X $item.X -Y $item.Y -W 110 -Primary
        $target = $item.Target
        $btn.Add_Click({
            # GetNewClosure() runs this in its own dynamic module, so assigning
            # $script:SelectedSitTarget here would set that module's copy and
            # leave the caller's empty. Carry the choice on the form instead;
            # mutating a shared object does cross the boundary.
            $popup.Tag = $target
            Write-Vfd2Log "SIT environment selected: $target"
            $popup.DialogResult = [System.Windows.Forms.DialogResult]::OK
            $popup.Close()
        }.GetNewClosure())
        $popup.Controls.Add($btn)
    }

    $btnCancel = New-StyledButton -Text 'Cancel' -X 330 -Y 200 -W 90
    $btnCancel.Add_Click({
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $popup.Close()
    })
    $popup.Controls.Add($btnCancel)
    $popup.CancelButton = $btnCancel

    $null = $popup.ShowDialog($script:Form)
    $script:SelectedSitTarget = [string]$popup.Tag
    return $popup.DialogResult
}

function Show-PuttyCredentialsPopup {
    $stored = Get-StoredPuttyCredentials
    if ($stored.user) { $script:PuttyUser = $stored.user }

    $popup = New-Object System.Windows.Forms.Form
    $popup.Text            = 'PuTTY Sign In'
    $popup.Size              = New-Object System.Drawing.Size(420, 260)
    $popup.StartPosition     = [System.Windows.Forms.FormStartPosition]::CenterParent
    $popup.FormBorderStyle   = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $popup.MaximizeBox       = $false
    $popup.MinimizeBox       = $false
    $popup.BackColor         = $script:ColorBg
    $popup.Font              = $script:FontBody

    $lblTitle = New-Object System.Windows.Forms.Label
    $lblTitle.Text      = 'Provide PuTTY User ID and Password'
    $lblTitle.Font      = $script:FontBold
    $lblTitle.ForeColor = $script:ColorText
    $lblTitle.Location  = New-Object System.Drawing.Point(24, 20)
    $lblTitle.Size      = New-Object System.Drawing.Size(360, 24)
    $popup.Controls.Add($lblTitle)

    $lblId = New-Object System.Windows.Forms.Label
    $lblId.Text     = 'PuTTY User ID:'
    $lblId.Location = New-Object System.Drawing.Point(24, 58)
    $lblId.Size     = New-Object System.Drawing.Size(100, 24)
    $popup.Controls.Add($lblId)

    $txtUser = New-Object System.Windows.Forms.TextBox
    $txtUser.Location    = New-Object System.Drawing.Point(130, 56)
    $txtUser.Size        = New-Object System.Drawing.Size(250, 28)
    $txtUser.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    if ($script:PuttyUser) { $txtUser.Text = $script:PuttyUser }
    $popup.Controls.Add($txtUser)

    $lblPass = New-Object System.Windows.Forms.Label
    $lblPass.Text     = 'Password:'
    $lblPass.Location = New-Object System.Drawing.Point(24, 98)
    $lblPass.Size     = New-Object System.Drawing.Size(100, 24)
    $popup.Controls.Add($lblPass)

    $txtPass = New-Object System.Windows.Forms.TextBox
    $txtPass.Location              = New-Object System.Drawing.Point(130, 96)
    $txtPass.Size                  = New-Object System.Drawing.Size(250, 28)
    $txtPass.BorderStyle           = [System.Windows.Forms.BorderStyle]::FixedSingle
    $txtPass.UseSystemPasswordChar = $true
    if ($stored.password) { $txtPass.Text = $stored.password }
    $popup.Controls.Add($txtPass)

    $btnOk = New-StyledButton -Text 'Continue' -X 130 -Y 148 -W 110 -Primary
    $btnCancel = New-StyledButton -Text 'Cancel' -X 260 -Y 148 -W 110
    $popup.Controls.Add($btnOk)
    $popup.Controls.Add($btnCancel)
    $popup.AcceptButton = $btnOk
    $popup.CancelButton = $btnCancel

    $btnOk.Add_Click({
        if ([string]::IsNullOrWhiteSpace($txtUser.Text)) {
            [System.Windows.Forms.MessageBox]::Show('Please enter PuTTY User ID.', 'Validation', 'OK', 'Warning') | Out-Null
            return
        }
        if ([string]::IsNullOrWhiteSpace($txtPass.Text)) {
            [System.Windows.Forms.MessageBox]::Show('Please enter PuTTY Password.', 'Validation', 'OK', 'Warning') | Out-Null
            return
        }
        $script:PuttyUser = $txtUser.Text.Trim()
        $script:PuttyPassword = $txtPass.Text
        Set-StoredPuttyCredentials -User $script:PuttyUser -Password $script:PuttyPassword
        Write-Vfd2Log "PuTTY credentials stored (user=$($script:PuttyUser))"
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $popup.Close()
    })

    $btnCancel.Add_Click({
        $popup.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $popup.Close()
    })

    $null = $popup.ShowDialog($script:Form)
    return $popup.DialogResult
}

function Confirm-CitrixSessionForSwitch {
    <#
        Three-way Citrix check, mirroring Invoke-DeployCitrixFlow:
          CASE A - KIAS desktop already open, nothing to ask for
          CASE B - desktop locked, sign in and unlock it
          CASE C - no desktop, sign in so sit_connect can open the Citrix URL
        Returns $false when the user cancels the sign-in.
    #>
    $script:DesktopAlreadyOpen = $false
    $script:DesktopLocked = $false
    Invoke-WithBusy -BusyText 'Citrix CASE A: checking for open KIAS desktop...' -ActionBlock {
        $focusResult = Invoke-Vfd2BackendSync -Action 'focus_desktop' -SkipDeploy
        if ($focusResult.ok) {
            $script:DesktopAlreadyOpen = $true
            Write-Vfd2Log "Citrix CASE A: existing desktop - $($focusResult.message)"
        } elseif ($focusResult.locked) {
            $script:DesktopLocked = $true
            Write-Vfd2Log 'Citrix CASE B: KIAS desktop is locked - will prompt for password'
        } else {
            Write-Vfd2Log 'Citrix CASE C: no KIAS desktop - Citrix sign-in required for SIT switch'
        }
    }

    if ($script:DesktopAlreadyOpen) { return $true }

    $citrixDialog = Show-CitrixSignInPopup
    if ($citrixDialog -ne [System.Windows.Forms.DialogResult]::OK) { return $false }

    if ($script:DesktopLocked) {
        Invoke-WithBusy -BusyText 'Citrix CASE B: unlocking desktop...' -ActionBlock {
            $unlockResult = Invoke-Vfd2BackendSync -Action 'focus_desktop' -SkipDeploy `
                -CitrixPassword $script:CitrixPassword
            Write-Vfd2Log "Citrix CASE B unlock: $($unlockResult.message)"
        }
    }

    return $true
}

function Invoke-SitEnvironmentConnect {
    param([string]$SitTarget)

    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show(
            'Please wait for the current action to finish.',
            'Busy', 'OK', 'Warning') | Out-Null
        return
    }

    $label = $SitTarget.ToUpper()
    if ($SitTarget -eq 'all_sit') { $label = 'All SIT environments' }

    Start-Vfd2BackendAction -Action 'sit_connect' `
        -SitTarget $SitTarget `
        -CitrixId $script:CitrixId `
        -CitrixPassword $script:CitrixPassword `
        -PuttyUser $script:PuttyUser `
        -PuttyPassword $script:PuttyPassword `
        -BusyText "Connecting PuTTY for $label..." `
        -SuccessStatus "Last: SIT switch $label completed" `
        -FailStatus 'SIT switch failed - see log'
}

function Invoke-SubmitSwitch {
    Write-Vfd2Log 'Submit clicked (or Enter pressed)'

    # Switching an environment needs no build parameters, so Submit asks for the
    # SIT target directly instead of going through the deploy form.
    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show(
            'Please wait for the current action to finish.',
            'Busy', 'OK', 'Warning') | Out-Null
        return
    }

    $choice = ''
    if ($script:txtChoice) { $choice = ([string]$script:txtChoice.Text).Trim() }
    if (-not $choice) {
        Write-Vfd2Log 'Submit ignored - no option entered'
        [System.Windows.Forms.MessageBox]::Show(
            'Enter an option (1-6) before clicking Submit.',
            'Option required', 'OK', 'Warning') | Out-Null
        if ($script:txtChoice) { $script:txtChoice.Focus() | Out-Null }
        return
    }
    Write-Vfd2Log "Submit option entered: $choice"

    $envDialog = Show-SitEnvironmentPopup
    if ($envDialog -ne [System.Windows.Forms.DialogResult]::OK) { return }
    if (-not $script:SelectedSitTarget) { return }

    # Settle Citrix before asking for PuTTY details, so a sign-in that is going
    # to be needed is not discovered after the credentials have been typed.
    if (-not (Confirm-CitrixSessionForSwitch)) { return }

    $puttyDialog = Show-PuttyCredentialsPopup
    if ($puttyDialog -ne [System.Windows.Forms.DialogResult]::OK) { return }

    Invoke-SitEnvironmentConnect -SitTarget $script:SelectedSitTarget
}

$btnUpload.Add_Click({
    Write-Vfd2Log 'Upload button clicked'
    Invoke-UploadFlow
})

$btnDeploy.Add_Click({
    Write-Vfd2Log 'Deploy button clicked'
    Invoke-DeployFlow
})

$btnSubmit.Add_Click({ Invoke-SubmitSwitch })

# Enter key triggers Submit from anywhere on the form
$script:Form.KeyPreview = $true
$script:Form.Add_KeyDown({
    param($sender, $e)
    if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        $e.Handled = $true
        $e.SuppressKeyPress = $true
        Invoke-SubmitSwitch
    }
})

$txtChoice.Add_KeyDown({
    param($sender, $e)
    if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        $e.Handled = $true
        $e.SuppressKeyPress = $true
        Invoke-SubmitSwitch
    }
})

# Session footer
$lblSession = New-Object System.Windows.Forms.Label
$lblSession.Text      = "Session: $($script:SessionId)"
$lblSession.Font      = $script:FontSmall
$lblSession.ForeColor = $script:ColorMuted
$lblSession.Dock      = [System.Windows.Forms.DockStyle]::Bottom
$lblSession.Height    = 24
$lblSession.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$script:Form.Controls.Add($lblSession)

$script:Form.Add_FormClosing({
    param($sender, $e)
    Write-Vfd2Log 'GUI close requested'
    $script:ForceCloseRequested = $true
    if ($script:ActiveBackendProcess -and -not $script:ActiveBackendProcess.HasExited) {
        try {
            Write-Vfd2Log 'Stopping active backend process so GUI can close'
            $script:ActiveBackendProcess.Kill()
            $script:ActiveBackendProcess = $null
        } catch {
            Write-Vfd2Log "WARNING: Could not stop backend process: $($_.Exception.Message)"
        }
    }
})

$script:Form.Add_FormClosed({
    Write-Vfd2Log '===== EnvPilot GUI closed ====='
})

[void][System.Windows.Forms.Application]::EnableVisualStyles()
$script:Form.Add_Load({
    try {
        $script:Form.WindowState = [System.Windows.Forms.FormWindowState]::Normal
        $script:Form.ShowInTaskbar = $true
        $script:Form.TopMost = $true
        $script:Form.BringToFront()
        $script:Form.Activate()
        [System.Windows.Forms.Application]::DoEvents()
        $script:Form.TopMost = $false
    } catch {}
})
Write-Vfd2Log 'Showing EnvPilot main window'
[void]$script:Form.ShowDialog()
