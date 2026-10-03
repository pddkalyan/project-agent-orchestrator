# PC Cleanup Discovery Procedure & Audit Specification

## Overview
This document specifies the read-only discovery procedure and inventory guidelines for Project Agent Orchestrator under the audit-only PC cleanup policy (`pc_cleanup_mode: AUDIT_ONLY`).

## Hard Constraints
- **Audit Only:** No deletion, uninstallation, registry modification, service termination, or file alteration is permitted during discovery or inventory.
- **Zero Spend:** No paid APIs or services.
- **Local AI Inference Prohibition:** No local video generation models or heavy weights stored outside approved cloud storage contracts.
- **Ownership Verification:** Every artifact must be categorized and evaluated for ownership evidence before any future removal consideration.

## Discovery Methodology
1. **Target Identification:**
   - Agent temporary directories (e.g. `~/.gemini/tmp`, project workspace `.git`, cache folders).
   - Python virtual environments (.venv, venv).
   - Local model caches or temporary render outputs.
2. **Evidence Gathering:**
   - Match candidate paths against `PROVENANCE_REGISTRY.jsonl` and task queue definitions.
   - Inspect creation and modification metadata.
   - Calculate disk utilization (size in bytes).
3. **Classification Distinction:**
   - **Verified Agent-Owned:** Explicitly recorded in `PROVENANCE_REGISTRY.jsonl`, created by agent tasks, or located inside designated project/temp directories with known task lineage.
   - **Unknown / User / System:** Unregistered files, user documents, system applications, OS components, or drivers. These are strictly classified as `UNSAFE_DO_NOT_REMOVE` and ignored for cleanup purposes.

## Output Format
The accompanying PowerShell script (`scripts/pc_cleanup_audit__created_by_worker_gemini_cli__model_gemini-3-flash-preview__task_TASK-0001.ps1`) produces a structured JSON inventory conforming to `schemas/pc_cleanup_audit.schema.json`.
