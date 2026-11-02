$ErrorActionPreference = 'Stop'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $administrator = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $administrator) {
        # Installed Apps normally starts unelevated. Keep confirmation interactive.
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
        $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    $installation = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installation.json') -Raw | ConvertFrom-Json
    if (-not $installation.data) { throw 'The trusted installation manifest has no data path.' }
    $root = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $root 'runtime\Scripts\python.exe'
    $cli = Join-Path $PSScriptRoot 'cli.py'
    # No --yes: the existing CLI must show paths and confirm permanent deletion.
    & $python $cli --data $installation.data uninstall
    $result = $LASTEXITCODE
    if ($result -ne 0) {
        Read-Host 'Uninstall failed. Review the error above. Press Enter to close' | Out-Null
    }
    exit $result
} catch {
    Write-Host ('Uninstall was not completed: ' + $_.Exception.Message)
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
