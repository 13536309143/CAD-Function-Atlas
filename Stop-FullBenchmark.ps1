param(
    [string]$Root = "$PSScriptRoot\outputs\full_benchmark_v1",
    [switch]$ListOnly
)
$ErrorActionPreference = 'Stop'
$resolvedBenchmarkRoot = (Resolve-Path -LiteralPath $Root).Path
$processSnapshot = Get-CimInstance Win32_Process
$ownedRoots = @($processSnapshot | Where-Object {
    $_.Name -in @('python.exe','pythonw.exe') -and
    $_.CommandLine -and
    $_.CommandLine -match [regex]::Escape($resolvedBenchmarkRoot) -and
    $_.CommandLine -match '(cad_benchmark|benchmark_supervisor)\.py'
})
$ownedIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($candidate in $ownedRoots) { [void]$ownedIds.Add([int]$candidate.ProcessId) }
do {
    $addedChild = $false
    foreach ($candidate in $processSnapshot) {
        if ($ownedIds.Contains([int]$candidate.ParentProcessId) -and $ownedIds.Add([int]$candidate.ProcessId)) { $addedChild = $true }
    }
} while ($addedChild)
if ($ListOnly) {
    $processSnapshot | Where-Object { $ownedIds.Contains([int]$_.ProcessId) } | Select-Object ProcessId,ParentProcessId,Name
    return
}
$benchmarkPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$controlScript = Join-Path $PSScriptRoot 'benchmark_control.py'
& $benchmarkPython $controlScript --root $resolvedBenchmarkRoot
if ($LASTEXITCODE -ne 0) { throw 'Failed to record stop; no process was terminated' }
foreach ($ownedId in $ownedIds) { Stop-Process -Id $ownedId -Force -ErrorAction SilentlyContinue }
& $benchmarkPython $controlScript --root $resolvedBenchmarkRoot
Write-Output ('Stopped only benchmark-owned process tree IDs: ' + (($ownedIds | Sort-Object) -join ','))
