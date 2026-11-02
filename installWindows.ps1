#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'NPF Server Manager requires x64 Windows 11.' }
if ([Environment]::OSVersion.Version.Build -lt 22000) { throw 'Windows 11 is required.' }
if ($args.Count -gt 0 -and $args[0] -eq 'uninstall') {
    $locator = Join-Path $env:ProgramData 'NPF-Server-Manager.json'
    if (-not (Test-Path -LiteralPath $locator)) { throw 'No registered NPF Server Manager installation was found.' }
    $installation = Get-Content -LiteralPath $locator -Raw | ConvertFrom-Json
    if (-not $installation.application_root -or -not $installation.data) { throw 'Rerun the installer once to register uninstall paths.' }
    $installedPython = Join-Path $installation.application_root 'runtime\Scripts\python.exe'
    $installedCli = Join-Path $installation.application_root 'manager\cli.py'
    if (-not (Test-Path -LiteralPath $installedPython) -or -not (Test-Path -LiteralPath $installedCli)) { throw 'Installed manager runtime or CLI is missing.' }
    $uninstallArgs = @($args | Select-Object -Skip 1)
    & $installedPython $installedCli --data $installation.data uninstall @uninstallArgs
    exit $LASTEXITCODE
}
$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = & py -3 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -ne 0 -or $python -like '*\Users\*') { $python = $null }
}
if (-not $python) {
    $target = Join-Path $env:ProgramFiles 'NPF\Python312'
    $python = Join-Path $target 'python.exe'
    if (-not (Test-Path $python)) {
        $download = Join-Path $env:TEMP 'npf-python-3.12.10-amd64.exe'
        Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $download
        $signature = Get-AuthenticodeSignature $download
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python installer signature verification failed.' }
        $process = Start-Process -Wait -PassThru -FilePath $download -ArgumentList @('/quiet','InstallAllUsers=1','Include_pip=1','Include_test=0',"TargetDir=`"$target`"")
        if ($process.ExitCode -notin @(0,3010)) { throw "Python installation failed: $($process.ExitCode)" }
        Remove-Item $download
    }
}
$bootstrap = Join-Path $env:ProgramData 'NPF-InstallerRuntime'
if (-not (Test-Path (Join-Path $bootstrap 'Scripts\python.exe'))) {
    & $python -m venv $bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare manager bootstrap environment.' }
}
$runtimePython = Join-Path $bootstrap 'Scripts\python.exe'
& $runtimePython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install Windows service/certificate prerequisites.' }
& $runtimePython (Join-Path $PSScriptRoot 'installer.py') @args
exit $LASTEXITCODE
