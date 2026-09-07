[CmdletBinding()]
param(
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runtimePath = Join-Path $projectRoot 'runtime'
$venvCommandPattern = [regex]::Escape(
    (Join-Path $projectRoot '.venv\Scripts')
)

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Lead Finder virtual environment is missing: $pythonPath"
}

function Find-LeadFinderProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandPattern
    )

    return Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match $venvCommandPattern -and
            $_.CommandLine -match $CommandPattern
        } |
        Select-Object -First 1
}

function Start-LeadFinderProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$CommandPattern,
        [Parameter(Mandatory = $true)]
        [string]$StdoutName,
        [Parameter(Mandatory = $true)]
        [string]$StderrName
    )

    $displayArguments = $Arguments -join ' '
    if ($WhatIf) {
        Write-Output "${Label}: would start $pythonPath $displayArguments"
        return
    }

    $running = Find-LeadFinderProcess -CommandPattern $CommandPattern
    if ($null -ne $running) {
        Write-Output "${Label}: already running (PID $($running.ProcessId))"
        return
    }

    if (-not (Test-Path -LiteralPath $runtimePath -PathType Container)) {
        New-Item -ItemType Directory -Path $runtimePath | Out-Null
    }
    $process = Start-Process -FilePath $pythonPath `
        -ArgumentList $Arguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtimePath $StdoutName) `
        -RedirectStandardError (Join-Path $runtimePath $StderrName) `
        -PassThru
    Write-Output "${Label}: started (PID $($process.Id))"
}

Start-LeadFinderProcess `
    -Label 'Crawl worker' `
    -Arguments @('-m', 'fb_crawl', 'worker', 'run', '--kind', 'crawl') `
    -CommandPattern '(?i)(?:-m\s+fb_crawl|fb-crawl\.exe"?)\s+worker\s+run\s+--kind\s+crawl(?:\s|$)' `
    -StdoutName 'crawl-worker.stdout.log' `
    -StderrName 'crawl-worker.stderr.log'

Start-LeadFinderProcess `
    -Label 'Export worker' `
    -Arguments @('-m', 'fb_crawl', 'worker', 'run', '--kind', 'export') `
    -CommandPattern '(?i)(?:-m\s+fb_crawl|fb-crawl\.exe"?)\s+worker\s+run\s+--kind\s+export(?:\s|$)' `
    -StdoutName 'export-worker.stdout.log' `
    -StderrName 'export-worker.stderr.log'

Start-LeadFinderProcess `
    -Label 'API' `
    -Arguments @('-m', 'fb_crawl', 'api', 'serve', '--host', '127.0.0.1', '--port', '8000') `
    -CommandPattern '(?i)(?:-m\s+fb_crawl|fb-crawl\.exe"?)\s+api\s+serve(?:\s|$)' `
    -StdoutName 'api.stdout.log' `
    -StderrName 'api.stderr.log'
