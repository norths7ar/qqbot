param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "logs")]
    [string]$Action = "status",

    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$botPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "bot.py"))
$runtimeDirectory = Join-Path $projectRoot "data\runtime"
$logDirectory = Join-Path $projectRoot "data\logs"
$statePath = Join-Path $runtimeDirectory "qqbot.json"
$lockPath = Join-Path $runtimeDirectory "qqbot.lock"

if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)

# uv-managed venvs on Windows launch the real interpreter as a child process
# from the base directory recorded in pyvenv.cfg, so validation must accept
# that child executable path as well as the .venv launcher.
$VenvBasePython = ""
$venvCfgPath = Join-Path $projectRoot ".venv\pyvenv.cfg"
if (Test-Path -LiteralPath $venvCfgPath) {
    $homeLine = Get-Content -LiteralPath $venvCfgPath |
        Where-Object { $_ -match "^\s*home\s*=\s*(.+)$" } |
        Select-Object -First 1
    if ($homeLine -and $homeLine -match "^\s*home\s*=\s*(.+)$") {
        $VenvBasePython = [System.IO.Path]::GetFullPath(
            (Join-Path $Matches[1].Trim() "python.exe")
        )
    }
}

function Get-ConfiguredPort {
    $processPort = 0
    if ($env:PORT -and [int]::TryParse($env:PORT, [ref]$processPort)) {
        return $processPort
    }

    $configuredPort = 8080
    $environmentPath = Join-Path $projectRoot ".env"
    if (Test-Path -LiteralPath $environmentPath) {
        $portLine = Get-Content -LiteralPath $environmentPath -Encoding utf8 |
            Where-Object { $_ -match "^\s*PORT\s*=\s*([0-9]+)\s*$" } |
            Select-Object -First 1
        if ($portLine -and $portLine -match "^\s*PORT\s*=\s*([0-9]+)\s*$") {
            $configuredPort = [int]$Matches[1]
        }
    }
    return $configuredPort
}

function Get-ListeningProcessIds {
    param([Parameter(Mandatory)][int]$Port)

    $netstatPath = Join-Path $env:SystemRoot "System32\netstat.exe"
    $lines = & $netstatPath @("-ano", "-p", "tcp")
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    $processIds = foreach ($line in $lines) {
        if ($line -notmatch "^\s*TCP\s+(\S+)\s+\S+\s+LISTENING\s+([0-9]+)\s*$") {
            continue
        }
        $localEndpoint = $Matches[1]
        $processId = [int]$Matches[2]
        if ($localEndpoint -match ":([0-9]+)$" -and [int]$Matches[1] -eq $Port) {
            $processId
        }
    }
    return @($processIds | Sort-Object -Unique)
}

function Get-LegacyBotProcess {
    $configuredPort = Get-ConfiguredPort
    $candidates = @()
    foreach ($processId in (Get-ListeningProcessIds -Port $configuredPort)) {
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
            $actualPython = [System.IO.Path]::GetFullPath($process.Path)
            if ($actualPython -eq $PythonPath -or
                ($VenvBasePython -and $actualPython -eq $VenvBasePython)) {
                $candidates += $process
            }
        }
        catch {
            continue
        }
    }

    if ($candidates.Count -eq 1) {
        return [PSCustomObject]@{
            Process = $candidates[0]
            Port = $configuredPort
        }
    }
    return $null
}

function Normalize-ProcessPathEnvironment {
    $variables = [System.Environment]::GetEnvironmentVariables()
    $pathKeys = @(
        $variables.Keys |
            Where-Object { [string]$_ -imatch "^path$" }
    )
    if ($pathKeys.Count -lt 2) {
        return
    }

    $pathValue = [string]$variables["Path"]
    if (-not $pathValue) {
        $pathValue = [string]$variables["PATH"]
    }
    [System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [System.Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

function Read-BotState {
    if (-not (Test-Path -LiteralPath $statePath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $statePath -Raw -Encoding utf8 |
            ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-ValidatedBotProcess {
    $state = Read-BotState
    if ($null -eq $state) {
        return $null
    }

    try {
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction Stop
        $actualPython = [System.IO.Path]::GetFullPath($process.Path)
        $recordedPython = [System.IO.Path]::GetFullPath([string]$state.python)
        $pythonMatches = $actualPython -eq $PythonPath
        if (-not $pythonMatches -and $VenvBasePython) {
            $pythonMatches = $actualPython -eq $VenvBasePython
        }
        if (-not $pythonMatches -or $recordedPython -ne $PythonPath) {
            return $null
        }

        $actualStart = $process.StartTime.ToUniversalTime()
        $recordedStart = [datetime]::Parse(
            [string]$state.started_at
        ).ToUniversalTime()
        $startDelta = ($recordedStart - $actualStart).TotalSeconds

        $legacyState = -not $state.PSObject.Properties["entrypoint"]
        if ($legacyState) {
            if ([math]::Abs($startDelta) -ge 2) {
                return $null
            }
        }
        else {
            $recordedEntrypoint = [System.IO.Path]::GetFullPath(
                [string]$state.entrypoint
            )
            if ($recordedEntrypoint -ne $botPath) {
                return $null
            }
            if ($startDelta -lt -1 -or $startDelta -gt 30) {
                return $null
            }
            if (-not (Test-BotLockHeld)) {
                return $null
            }
        }

        return [PSCustomObject]@{
            Process = $process
            State = $state
            Legacy = $legacyState
        }
    }
    catch {
        return $null
    }
}

function Test-BotLockHeld {
    if (-not (Test-Path -LiteralPath $lockPath)) {
        return $false
    }

    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        return $false
    }
    catch [System.IO.IOException] {
        return $true
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Remove-StaleState {
    if ((Test-Path -LiteralPath $statePath) -and -not (Test-BotLockHeld)) {
        Remove-Item -LiteralPath $statePath -Force
    }
}

function Get-RecentLogPaths {
    $managed = Get-ValidatedBotProcess
    if ($null -ne $managed) {
        $stdout = [string]$managed.State.stdout
        $stderr = [string]$managed.State.stderr
        if ($stdout -or $stderr) {
            return [PSCustomObject]@{ Stdout = $stdout; Stderr = $stderr }
        }
    }

    $logParameters = @{
        LiteralPath = $logDirectory
        Filter = "qqbot-*.stdout.log"
        File = $true
        ErrorAction = "SilentlyContinue"
    }
    $stdoutFile = Get-ChildItem @logParameters |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -ne $stdoutFile) {
        $stderrFile = $stdoutFile.FullName -replace "\.stdout\.log$", ".stderr.log"
        return [PSCustomObject]@{
            Stdout = $stdoutFile.FullName
            Stderr = $stderrFile
        }
    }

    return [PSCustomObject]@{
        Stdout = Join-Path $logDirectory "qqbot.stdout.log"
        Stderr = Join-Path $logDirectory "qqbot.stderr.log"
    }
}

function Get-StartupFailure {
    param(
        [Parameter(Mandatory)][string]$StdoutPath,
        [Parameter(Mandatory)][string]$StderrPath
    )

    $lines = @()
    if (Test-Path -LiteralPath $StdoutPath) {
        $lines += Get-Content -LiteralPath $StdoutPath -Tail 25 -Encoding utf8
    }
    if (Test-Path -LiteralPath $StderrPath) {
        $lines += Get-Content -LiteralPath $StderrPath -Tail 25 -Encoding utf8
    }
    if ($lines.Count -eq 0) {
        return "No startup output was captured."
    }
    return ($lines -join [Environment]::NewLine)
}

function Start-Bot {
    $existing = Get-ValidatedBotProcess
    if ($null -ne $existing) {
        Write-Output "qqbot is already running. PID: $($existing.Process.Id)"
        return
    }
    $legacy = Get-LegacyBotProcess
    if ($null -ne $legacy) {
        Write-Output (
            "qqbot is already running as a legacy unmanaged process. " +
            "PID: $($legacy.Process.Id). Port: $($legacy.Port). " +
            "Use restart to replace it with a managed instance."
        )
        return
    }
    if (Test-BotLockHeld) {
        throw "qqbot runtime lock is held, but its process state is invalid. Refusing to start a duplicate process."
    }
    Remove-StaleState
    $startPort = Get-ConfiguredPort
    $portOccupiers = Get-ListeningProcessIds -Port $startPort
    if ($portOccupiers.Count -gt 0) {
        $occupierDetails = foreach ($occupierPid in $portOccupiers) {
            $occupierProcess = Get-Process -Id $occupierPid -ErrorAction SilentlyContinue
            if ($null -ne $occupierProcess) {
                "PID $occupierPid ($($occupierProcess.ProcessName))"
            }
            else {
                "PID $occupierPid"
            }
        }
        throw (
            "Port $startPort is already in use by $($occupierDetails -join ', '). " +
            'Refusing to start qqbot on an occupied port. Stop the other process or change PORT.'
        )
    }

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        throw "Cannot find the qqbot Python interpreter: $PythonPath"
    }
    if (-not (Test-Path -LiteralPath $botPath)) {
        throw "Cannot find the qqbot entrypoint: $botPath"
    }

    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Normalize-ProcessPathEnvironment

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $stdoutPath = Join-Path $logDirectory "qqbot-$timestamp.stdout.log"
    $stderrPath = Join-Path $logDirectory "qqbot-$timestamp.stderr.log"
    $env:QQBOT_STDOUT_PATH = $stdoutPath
    $env:QQBOT_STDERR_PATH = $stderrPath
    $quotedBotPath = '"' + $botPath.Replace('"', '\"') + '"'
    $startParameters = @{
        FilePath = $PythonPath
        ArgumentList = @($quotedBotPath)
        WorkingDirectory = $projectRoot
        WindowStyle = "Hidden"
        RedirectStandardOutput = $stdoutPath
        RedirectStandardError = $stderrPath
        PassThru = $true
    }
    $process = Start-Process @startParameters

    $managed = $null
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Milliseconds 200
        $process.Refresh()
        if ($process.HasExited) {
            $failureParameters = @{
                StdoutPath = $stdoutPath
                StderrPath = $stderrPath
            }
            $failure = Get-StartupFailure @failureParameters
            $message = "qqbot exited during startup." +
                [Environment]::NewLine + $failure
            throw $message
        }
        $managed = Get-ValidatedBotProcess
        if ($null -ne $managed) {
            break
        }
    }

    if ($null -eq $managed) {
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
        throw "qqbot started without publishing a valid process state and was stopped."
    }

    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) {
        $failureParameters = @{
            StdoutPath = $stdoutPath
            StderrPath = $stderrPath
        }
        $failure = Get-StartupFailure @failureParameters
        $message = "qqbot exited during startup." +
            [Environment]::NewLine + $failure
        throw $message
    }

    Write-Output "qqbot started in background. PID: $($process.Id)"
    Write-Output "Stdout: $stdoutPath"
    Write-Output "Stderr: $stderrPath"
}

function Stop-Bot {
    $managed = Get-ValidatedBotProcess
    if ($null -eq $managed) {
        if (Test-BotLockHeld) {
            throw "qqbot runtime lock is held, but its process state is invalid. Refusing to stop an unidentified process."
        }
        $legacy = Get-LegacyBotProcess
        if ($null -ne $legacy) {
            $legacyPid = $legacy.Process.Id
            Stop-Process -Id $legacyPid -ErrorAction Stop
            $legacy.Process.WaitForExit(10000) | Out-Null
            Remove-StaleState
            Write-Output (
                "qqbot legacy process stopped. PID: $legacyPid. " +
                "It was identified by port $($legacy.Port) and the exact qqbot Python interpreter."
            )
            return
        }
        Remove-StaleState
        Write-Output "qqbot is not running."
        return
    }

    $pidToStop = $managed.Process.Id
    Stop-Process -Id $pidToStop -ErrorAction Stop
    $managed.Process.WaitForExit(10000) | Out-Null

    for ($attempt = 0; $attempt -lt 20 -and (Test-BotLockHeld); $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-BotLockHeld) {
        throw "qqbot process exited, but the runtime lock is still held."
    }
    Remove-StaleState
    Write-Output "qqbot stopped. PID: $pidToStop"
}

function Show-BotStatus {
    $managed = Get-ValidatedBotProcess
    if ($null -ne $managed) {
        $stateKind = if ($managed.Legacy) { "legacy" } else { "current" }
        Write-Output (
            "qqbot is running. PID: $($managed.Process.Id). " +
            "Started: $($managed.Process.StartTime). State: $stateKind."
        )
        return
    }
    $legacy = Get-LegacyBotProcess
    if ($null -ne $legacy) {
        Write-Output (
            "qqbot is running as a legacy unmanaged process. " +
            "PID: $($legacy.Process.Id). Started: $($legacy.Process.StartTime). " +
            "Port: $($legacy.Port)."
        )
        return
    }
    if (Test-BotLockHeld) {
        Write-Output "qqbot is running or starting, but its process state is invalid."
        exit 2
    }
    Remove-StaleState
    Write-Output "qqbot is not running."
    exit 1
}

function Show-BotLogs {
    $paths = Get-RecentLogPaths
    $shown = $false
    if ($paths.Stdout -and (Test-Path -LiteralPath $paths.Stdout)) {
        Write-Output "=== stdout: $($paths.Stdout) ==="
        Get-Content -LiteralPath $paths.Stdout -Tail 80 -Encoding utf8
        $shown = $true
    }
    if ($paths.Stderr -and (Test-Path -LiteralPath $paths.Stderr)) {
        Write-Output "=== stderr: $($paths.Stderr) ==="
        Get-Content -LiteralPath $paths.Stderr -Tail 80 -Encoding utf8
        $shown = $true
    }
    $auditPath = Join-Path $logDirectory "qqbot-audit.jsonl"
    if (Test-Path -LiteralPath $auditPath) {
        Write-Output "=== audit: $auditPath ==="
        Get-Content -LiteralPath $auditPath -Tail 80 -Encoding utf8
        $shown = $true
    }
    if (-not $shown) {
        Write-Output "No background logs yet."
    }
}

switch ($Action) {
    "start" { Start-Bot }
    "stop" { Stop-Bot }
    "restart" {
        Stop-Bot
        Start-Bot
    }
    "status" { Show-BotStatus }
    "logs" { Show-BotLogs }
}
