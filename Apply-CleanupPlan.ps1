param(
    [string]$Manifest = "$PSScriptRoot\outputs\publication\cleanup_plan.json",
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$workspacePath = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$dataPath = Join-Path $workspacePath 'data'
$benchmarkPath = Join-Path $workspacePath 'outputs\full_benchmark_v1'
if ($plan.workspace -ne $workspacePath -or $plan.root -ne $benchmarkPath) { throw 'Manifest root differs from this workspace' }
$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
# Verify ALL targets before the first deletion. No dynamically built shell commands.
foreach ($target in $plan.targets) {
    $absolutePath = (Resolve-Path -LiteralPath $target.path).Path
    $expectedParent = switch ($target.kind) {
        'model' { $dataPath }
        'asset_directory' { Join-Path $benchmarkPath 'assets' }
        'job_directory' { Join-Path $benchmarkPath 'jobs' }
        default { throw 'Unknown deletion kind' }
    }
    if (-not $absolutePath.StartsWith($expectedParent + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw "Outside approved subtree: $absolutePath" }
    if (-not $seen.Add($absolutePath)) { throw "Duplicate target: $absolutePath" }
    $item = Get-Item -LiteralPath $absolutePath -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point: $absolutePath" }
    if ($target.kind -eq 'model') {
        if ($item.PSIsContainer -or (Get-FileHash -LiteralPath $absolutePath -Algorithm SHA256).Hash -ne $target.sha256) { throw "Model hash/type changed: $absolutePath" }
    } else {
        if (-not $item.PSIsContainer) { throw "Expected directory: $absolutePath" }
        $children = @(Get-ChildItem -LiteralPath $absolutePath -Recurse -Force)
        if (@($children | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -gt 0) { throw "Nested reparse point: $absolutePath" }
        if (@($children | Where-Object { -not $_.PSIsContainer }).Count -ne $target.file_count) { throw "Directory changed: $absolutePath" }
    }
}
if (-not $Apply) { Write-Output ("Verified {0} targets, {1} never-attempted models; rerun with -Apply to delete." -f $plan.target_count,$plan.removed_models); return }
foreach ($target in $plan.targets) {
    if ($target.kind -eq 'model') { Remove-Item -LiteralPath $target.path -Force }
    else { Remove-Item -LiteralPath $target.path -Recurse -Force }
}
$receipt = @{ completed_at = (Get-Date).ToString('o'); removed_models = $plan.removed_models; target_count = $plan.target_count; bytes = $plan.bytes; manifest = $Manifest }
$receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path (Split-Path -Parent $Manifest) 'cleanup_completed.json') -Encoding UTF8
Write-Output ("Deleted exactly {0} models and their exclusive generated directories." -f $plan.removed_models)
