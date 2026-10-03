# PC Cleanup Discovery and Audit Specification

**Task ID:** TASK-0001  
**Worker Model:** gemini-3.1-flash-lite  
**Status:** PENDING_ASTRA_REVIEW  

## Overview
This document defines the safe, read-only PC cleanup audit architecture for Project Agent Orchestrator. In accordance with project safety mandates, local PC cleanup is strictly **audit-only**. No files, directories, registries, or services are deleted, uninstalled, or modified by this discovery tool.

## Safety Controls & Guardrails
1. **Zero-Spend Constraint:** USD 0.00 spend. No paid APIs or services are invoked.
2. **Cloud-Only Inference:** No local AI video generation or heavy local model execution.
3. **Audit-Only Policy:** Deletion and removal actions are strictly forbidden. The discovery script and process only output structured reports.
4. **Exact-Path Provenance Matching:** Provenance verification requires an exact normalized path match against the installation ledger (`PROVENANCE_REGISTRY.jsonl`). Filename-only guessing or heuristics (e.g., matching partial names) are strictly prohibited.
5. **Explicit Candidate Roots (`AgentOwnedCandidateRoots`)**: The audit script does not perform arbitrary scans of home directories, system folders, or unrelated paths by default. Candidate roots for review must be explicitly supplied as parameters. By default, `AgentOwnedCandidateRoots` is empty.
6. **Active Protection (`KEEP_ACTIVE`)**: All active orchestrator directories (`.git`, `memory`, `tasks`, `docs`, `triggers`, `review_packets`, `schemas`, `scripts`) and root control files are strictly classified as `KEEP_ACTIVE`. Current trigger files and worker result files within the active project are never proposed for removal.
7. **Default Output to Stdout**: The audit script outputs structured JSON reports to standard output by default. Writing to a file requires an explicitly provided `OutputPath`.

## Classification Logic
- **`KEEP_ACTIVE`**: Assigned to active orchestrator infrastructure, control files, current triggers, and core directories.
- **`CANDIDATE_REVIEW_ONLY`**: Assigned only when there is an exact provenance/installation ledger match OR the path resides under an explicitly supplied `AgentOwnedCandidateRoot`.
- **`UNVERIFIED_DO_NOT_REMOVE`**: Assigned to any unknown, user, system, application, driver, registry, or unrelated path.
