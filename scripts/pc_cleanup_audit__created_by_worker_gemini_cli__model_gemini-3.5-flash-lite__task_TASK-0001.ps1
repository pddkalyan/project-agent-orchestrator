[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [string]$ActiveProjectRoot = (Get-Location).Path,

    [Parameter(Mandatory=$false)]
    [string[]]$AgentOwnedCandidateRoots = @(),

    [Parameter(Mandatory=$false)]
    [string]$OutputPath = $null
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "Error: PowerShell 7+ is required to run this script (found PowerShell $($PSVersionTable.PSVersion)). Please run with pwsh."
    exit 1
}

$ResolvedActiveProjectRoot = [System.IO.Path]::GetFullPath($ActiveProjectRoot)
if (-not (Test-Path -LiteralPath $ResolvedActiveProjectRoot -PathType Container)) {
    throw "ActiveProjectRoot does not exist or is not a directory: $ResolvedActiveProjectRoot"
}

$ValidatedCandidateRoots = @()
foreach ($root in @($AgentOwnedCandidateRoots)) {
    if ([string]::IsNullOrWhiteSpace($root)) { continue }
    $fullRoot = [System.IO.Path]::GetFullPath($root)
    if (-not (Test-Path -LiteralPath $fullRoot -PathType Container)) {
        throw "AgentOwnedCandidateRoot does not exist or is not a directory: $fullRoot"
    }
    $ValidatedCandidateRoots += $fullRoot
}

$provenanceSet = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$provPath = Join-Path $ResolvedActiveProjectRoot "PROVENANCE_REGISTRY.jsonl"
if (Test-Path -LiteralPath $provPath) {
    foreach ($line in (Get-Content -LiteralPath $provPath -Encoding utf8)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $record = $line | ConvertFrom-Json
            if ($record.file) {
                $filePath = $record.file
                if (-not [System.IO.Path]::IsPathRooted($filePath)) {
                    $filePath = Join-Path $ResolvedActiveProjectRoot $filePath
                }
                $norm = [System.IO.Path]::GetFullPath($filePath)
                [void]$provenanceSet.Add($norm)
            }
        } catch {
            # Ignore unparseable lines
        }
    }
}

function Test-IsActiveProjectFile {
    param([string]$filePath, [string]$activeRoot)
    $full = [System.IO.Path]::GetFullPath($filePath)
    $normActive = [System.IO.Path]::GetFullPath($activeRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($full.StartsWith($normActive, [System.StringComparison]::OrdinalIgnoreCase) -or $full -eq $normActive.TrimEnd([System.IO.Path]::DirectorySeparatorChar)) {
        return $true
    }
    return $false
}

$scannedFiles = [System.Collections.Generic.Dictionary[string, [PSCustomObject]]]::new([StringComparer]::OrdinalIgnoreCase)

foreach ($root in $ValidatedCandidateRoots) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Candidate root is not a valid directory: $root"
    }
    $files = Get-ChildItem -LiteralPath $root -Force -Recurse -File -ErrorAction Stop
    foreach ($file in $files) {
        $fullPath = [System.IO.Path]::GetFullPath($file.FullName)
        if ($scannedFiles.ContainsKey($fullPath)) {
            continue
        }

        $isActive = Test-IsActiveProjectFile -filePath $fullPath -activeRoot $ResolvedActiveProjectRoot
        $provMatch = $provenanceSet.Contains($fullPath)

        $recommendation = "UNVERIFIED_DO_NOT_REMOVE"
        $evidenceSource = "EXPLICIT_AGENT_OWNED_ROOT"
        $reason = "External candidate root file without exact provenance match."

        if ($isActive) {
            $recommendation = "KEEP_ACTIVE"
            $evidenceSource = "ACTIVE_PROJECT"
            $reason = "Active project infrastructure file protected from removal."
        } elseif ($provMatch) {
            $recommendation = "CANDIDATE_REVIEW_ONLY"
            $evidenceSource = "EXACT_PROVENANCE_MATCH"
            $reason = "Exact provenance match found in PROVENANCE_REGISTRY.jsonl under external candidate root."
        }

        $relPath = $file.Name
        if ($isActive) {
            $relPath = [System.IO.Path]::GetRelativePath($ResolvedActiveProjectRoot, $fullPath)
        } else {
            $relPath = [System.IO.Path]::GetRelativePath($root, $fullPath)
        }
        $relPath = $relPath -replace '/', [System.IO.Path]::DirectorySeparatorChar

        $itemObj = [PSCustomObject]@{
            path               = $relPath
            absolute_path      = $fullPath
            size_bytes         = [int64]$file.Length
            last_modified_utc  = $file.LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
            provenance_match   = [bool]$provMatch
            evidence_source    = $evidenceSource
            recommendation     = $recommendation
            reason             = $reason
        }

        $scannedFiles[$fullPath] = $itemObj
    }
}

$itemsList = @($scannedFiles.Values)

$keepActiveCount = @($itemsList | Where-Object { $_.recommendation -eq "KEEP_ACTIVE" }).Count
$candidateReviewCount = @($itemsList | Where-Object { $_.recommendation -eq "CANDIDATE_REVIEW_ONLY" }).Count
$unverifiedCount = @($itemsList | Where-Object { $_.recommendation -eq "UNVERIFIED_DO_NOT_REMOVE" }).Count

$report = [PSCustomObject]@{
    schema_version             = 1
    audit_timestamp            = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    active_project_root        = $ResolvedActiveProjectRoot
    agent_owned_candidate_roots = @($ValidatedCandidateRoots)
    summary                    = [PSCustomObject]@{
        total_scanned                = $itemsList.Count
        keep_active_count            = $keepActiveCount
        candidate_review_only_count  = $candidateReviewCount
        unverified_do_not_remove_count = $unverifiedCount
    }
    items                      = $itemsList
}

$jsonOutput = $report | ConvertTo-Json -Depth 10

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    $fs = [System.IO.FileStream]::new($resolvedOutput, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $sw = [System.IO.StreamWriter]::new($fs, [System.Text.UTF8Encoding]::new($false))
        try {
            $sw.Write($jsonOutput)
        } finally {
            $sw.Dispose()
        }
    } finally {
        $fs.Dispose()
    }
} else {
    $jsonOutput
}
