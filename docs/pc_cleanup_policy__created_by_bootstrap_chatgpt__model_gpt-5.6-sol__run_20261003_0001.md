# PC Cleanup Safety Policy

## Phase 1: audit only
Inventory likely prior agent-created artifacts, including project folders, virtual environments, downloaded models, caches, containers, temporary renders, and agent-created services/processes.

## Evidence required before deletion
At least one strong ownership signal is required, preferably multiple:
- provenance/installation ledger
- known project path
- agent-created manifest
- explicit prior task record
- matching process/environment metadata

## Never auto-delete
User documents, unrelated applications, Windows components, drivers, registry entries, unknown folders, or anything with uncertain ownership.

## Future cleanup
Every new agent installation must record path, purpose, experiment ID, size, dependencies, whether active, and safe-removal procedure.
