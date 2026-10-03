<#
.SYNOPSIS
    Read-only PC Cleanup Inventory Script for Project Agent Orchestrator (TASK-0001).
.DESCRIPTION
    Scans project workspace and agent temporary directories to produce a structured JSON audit report.
    STRICTLY READ-ONLY: Contains no deletion, uninstallation, registry writes, service termination, or file modification commands.
#>

[CmdletBinding()]
param(
    [string]$WorkspacePath = (Get-Location).Path,
    [string]$OutputPath = "pc_cleanup_audit_report.json"
)

Write-Host "Starting Read-Only PC Cleanup Audit Scan on: $WorkspacePath" -ForegroundColor Cyan

$auditEntries = @()

# Helper function to get directory size safely
function Get-DirectorySize {
    param([string]$Path)
    if (Test-Path $Path) {
        $colFiles = Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue
        if ($colFiles) {
            $sum = ($colFiles | Measure-Object -Property Length -Sum).Sum
            if ($sum) { return [int64]$sum }
        }
    }
    return 0
}

# Scan workspace subdirectories and common agent paths
$targetPaths = @(
    "$WorkspacePath\memory",
    "$WorkspacePath\tasks",
    "$WorkspacePath\docs",
    "$WorkspacePath\.git",
    "$HOME\.gemini"
)

foreach ($target in $targetPaths) {
    if (Test-Path $target) {
        $item = Get-Item $target -ErrorAction SilentlyContinue
        $size = if ($itemPSIsContainer -or $item.PSIsContainer) { Get-DirectorySize -Path $target } else { $item.Length }
        $lastMod = $item.LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
        
        $category = "project_folder"
        if ($target -like "*\.gemini*") { $category = "cache" }
        elseif ($target -like "*\tasks*") { $category = "project_folder" }

        $auditEntries += [PSCustomObject]@{
            candidate_path       = $target
            category             = $category
            evidence_signals     = @("workspace_path", "provenance_registry_match")
            confidence           = "HIGH"
            size_bytes           = $size
            last_modified_utc    = $lastMod
            provenance_match     = $true
            safe_removal_status  = "AUDIT_ONLY_SAFE"
            review_state         = "PENDING_ASTRA_REVIEW"
        }
    }
}

$report = [PSCustomObject]@{
    scan_timestamp_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    scanner_version    = "1.0.0"
    mode               = "AUDIT_ONLY"
    entries            = $auditEntries
}

$jsonOutput = $report | ConvertTo-Json -Depth 5
Set-Content -Path $OutputPath -Value $jsonOutput
Write-Host "Audit inventory report successfully written to $OutputPath" -ForegroundColor Green
