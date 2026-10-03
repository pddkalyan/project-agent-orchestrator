<#
.SYNOPSIS
    Read-only PC Cleanup Audit Script for Project Agent Orchestrator.
.DESCRIPTION
    Performs a non-destructive audit of project files and optional agent-owned candidate roots.
    Classifies paths into KEEP_ACTIVE, CANDIDATE_REVIEW_ONLY, or UNVERIFIED_DO_NOT_REMOVE.
    Strictly read-only: performs zero modifications, deletions, service stops, or registry updates.
.PARAMETER ActiveProjectRoot
    The root path of the active project. Defaults to current directory.
.PARAMETER AgentOwnedCandidateRoots
    Explicit list of directories to consider as candidate roots for review. Defaults to empty.
.PARAMETER OutputPath
    Optional path to write the JSON audit report. If omitted, output is printed to stdout.
#>

param(
    [string]$ActiveProjectRoot = (Resolve-Path .),
    [string[]]$AgentOwnedCandidateRoots = @(),
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

# Ensure normalized paths
$resolvedActiveRoot = [System.IO.Path]::GetFullPath($ActiveProjectRoot)
$normalizedCandidateRoots = foreach ($root in $AgentOwnedCandidateRoots) {
    if ([string]::IsNullOrWhiteSpace($root)) { continue }
    [System.IO.Path]::GetFullPath($root)
}

# Load provenance registry if present for exact-path matching
$provenanceRegistryPath = Join-Path $resolvedActiveRoot "PROVENANCE_REGISTRY.jsonl"
$ledgerPaths = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

if (Test-Path $provenanceRegistryPath) {
    foreach ($line in Get-Content $provenanceRegistryPath) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $record = $line | ConvertFrom-Json
            if ($record.file) {
                $fullLedgerPath = [System.IO.Path]::GetFullPath((Join-Path $resolvedActiveRoot $record.file))
                [void]$ledgerPaths.Add($fullLedgerPath)
            }
        } catch {
            # Ignore parsing errors for malformed lines
        }
    }
}

$activeOrchestratorDirs = @(".git", "memory", "tasks", "docs", "triggers", "review_packets", "schemas", "scripts")
$activeControlFiles = @("AGENTS.md", "GEMINI.md", "README.md", "PROVENANCE_REGISTRY.jsonl", "worker_result_TASK-0001.json")

$items = @()

# Function to check if a path is under active orchestrator project
function Test-IsActiveOrchestrator {
    param([string]$FullPath)
    $relPath = [System.IO.Path]::GetRelativePath($resolvedActiveRoot, $FullPath)
    if ($relPath.StartsWith("..")) { return $false }
    
    $topLevel = $relPath.Split([System.IO.Path]::DirectorySeparatorChar)[0]
    if ($activeOrchestratorDirs -contains $topLevel) { return $true }
    if ($activeControlFiles -contains $relPath) { return $true }
    return $false
}

# Function to check if a path is under explicit candidate roots
function Test-IsUnderCandidateRoot {
    param([string]$FullPath)
    foreach ($candRoot in $normalizedCandidateRoots) {
        if ($FullPath.Equals($candRoot, [StringComparison]::OrdinalIgnoreCase) -or $FullPath.StartsWith($candRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

# Scan Active Project Root recursively
if (Test-Path $resolvedActiveRoot) {
    $allFiles = Get-ChildItem -Path $resolvedActiveRoot -Recurse -File -Force -ErrorAction SilentlyContinue
    foreach ($file in $allFiles) {
        $fullPath = $file.FullName
        $relPath = [System.IO.Path]::GetRelativePath($resolvedActiveRoot, $fullPath)
        $size = $file.Length
        
        $isActive = Test-IsActiveOrchestrator -FullPath $fullPath
        $isLedgerMatched = $ledgerPaths.Contains($fullPath)
        $isUnderCandidate = Test-IsUnderCandidateRoot -FullPath $fullPath

        $classification = "UNVERIFIED_DO_NOT_REMOVE"
        $reason = "Unverified file path."

        if ($isActive) {
            $classification = "KEEP_ACTIVE"
            $reason = "Part of active orchestrator infrastructure or control files."
        } elseif ($isLedgerMatched -or $isUnderCandidate) {
            $classification = "CANDIDATE_REVIEW_ONLY"
            $reason = if ($isLedgerMatched) { "Exact match in PROVENANCE_REGISTRY.jsonl ledger." } else { "Contained under explicitly supplied AgentOwnedCandidateRoot." }
        }

        $items += [PSCustomObject]@{
            path              = $relPath
            classification    = $classification
            reason            = $reason
            provenance_match  = $isLedgerMatched
            size_bytes        = $size
        }
    }
}

# Scan explicit candidate roots if any provided
foreach ($candRoot in $normalizedCandidateRoots) {
    if (Test-Path $candRoot) {
        $candFiles = Get-ChildItem -Path $candRoot -Recurse -File -Force -ErrorAction SilentlyContinue
        foreach ($file in $candFiles) {
            $fullPath = $file.FullName
            # Avoid duplicating if already scanned via active root
            $alreadyScanned = $items | Where-Object { [System.IO.Path]::GetFullPath((Join-Path $resolvedActiveRoot $_.path)) -eq $fullPath }
            if (-not $alreadyScanned) {
                $isLedgerMatched = $ledgerPaths.Contains($fullPath)
                $classification = if ($isLedgerMatched) { "CANDIDATE_REVIEW_ONLY" } else { "UNVERIFIED_DO_NOT_REMOVE" }
                $reason = if ($isLedgerMatched) { "Exact match in PROVENANCE_REGISTRY.jsonl ledger under candidate root." } else { "Under explicit candidate root but lacks ledger provenance match." }

                $items += [PSCustomObject]@{
                    path              = $fullPath
                    classification    = $classification
                    reason            = $reason
                    provenance_match  = $isLedgerMatched
                    size_bytes        = $file.Length
                }
            }
        }
    }
}

$keepActiveCount = ($items | Where-Object { $_.classification -eq "KEEP_ACTIVE" }).Count
$candidateReviewCount = ($items | Where-Object { $_.classification -eq "CANDIDATE_REVIEW_ONLY" }).Count
$unverifiedCount = ($items | Where-Object { $_.classification -eq "UNVERIFIED_DO_NOT_REMOVE" }).Count

$report = [PSCustomObject]@{
    schema_version              = 1
    audit_timestamp             = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
    worker_model                = "gemini-3.1-flash-lite"
    active_project_root         = $resolvedActiveRoot
    agent_owned_candidate_roots = $normalizedCandidateRoots
    summary                     = [PSCustomObject]@{
        total_scanned                 = $items.Count
        keep_active_count             = $keepActiveCount
        candidate_review_only_count   = $candidateReviewCount
        unverified_do_not_remove_count = $unverifiedCount
    }
    items                       = $items
}

$jsonOutput = $report | ConvertTo-Json -Depth 10

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $parentDir = Split-Path $OutputPath -Parent
    if ($parentDir -and -not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    Set-Content -Path $OutputPath -Value $jsonOutput -Encoding utf8
    Write-Host "Audit report written to $OutputPath"
} else {
    Write-Output $jsonOutput
}
