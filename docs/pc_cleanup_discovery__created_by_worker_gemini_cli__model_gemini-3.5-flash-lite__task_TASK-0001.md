# PC Cleanup Discovery & Audit Architecture (TASK-0001)

## Executive Summary
This document outlines the design, safety guarantees, and architecture for the safe PC cleanup audit package (`TASK-0001`). In accordance with Project Agent Orchestrator policy, cleanup is **audit-only**. No files are deleted, uninstalled, modified, or moved automatically.

## Core Safety Constraints
1. **Zero-Spend**: All operations run locally or via free cloud workflows without incurring financial costs.
2. **Audit Only**: Absolutely no automated deletion, uninstallation, service termination, process termination, registry modification, or system changes.
3. **PowerShell 7+ Requirement**: Requires modern PowerShell 7+ (`pwsh`) for robust object pipelines, strict error handling (`-ErrorAction Stop`), and atomic file I/O. Windows PowerShell 5.1 is explicitly rejected with a clear prerequisite message.
4. **Active Infrastructure Protection**: Active project files (`.github`, `.gitignore`, control files, memory, triggers, and `worker_result_TASK-*.json` anywhere beneath `ActiveProjectRoot`) are strictly protected and classified as `KEEP_ACTIVE`.
5. **Exact Provenance Matching**: Scanned files under candidate roots are cross-checked against `PROVENANCE_REGISTRY.jsonl` using case-insensitive normalized absolute paths.
6. **Atomic Output Creation**: When an output path is specified, reports are written atomically using `[System.IO.FileStream]` with `FileMode::CreateNew`, failing safely if the destination already exists.

## Classification Taxonomy
- `KEEP_ACTIVE`: Active project infrastructure. Must never be removed.
- `CANDIDATE_REVIEW_ONLY`: External candidate file with an exact provenance match in `PROVENANCE_REGISTRY.jsonl`. Requires manual review before any future action.
- `UNVERIFIED_DO_NOT_REMOVE`: External file without provenance proof. Must not be touched.

## Evidence Sources
- `ACTIVE_PROJECT`
- `EXACT_PROVENANCE_MATCH`
- `EXPLICIT_AGENT_OWNED_ROOT`
