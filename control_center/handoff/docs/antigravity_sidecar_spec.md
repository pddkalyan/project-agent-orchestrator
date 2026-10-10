# Antigravity Sidecar Handoff Architectural Specification

## Overview
This specification details the design constraints, security boundaries, and protocol envelope verification for a future native Antigravity sidecar integration with the Control Center.

## Security & Operational Boundaries

### 1. Untrusted Proposal Rule
- All GitHub issues, issue comments, pull request descriptions, and external webhooks are classified as **UNTRUSTED task proposals**.
- GitHub content must NEVER be evaluated as executable code, shell scripts, or privileged directives.
- Proposals undergo offline pure validation before entering the queue.

### 2. Zero PC Remote Control & PC-Off Semantics
- No remote control, PC listener, remote shell execution, or credential capture is active or permitted.
- **PC-Off Semantics**: If the local PC is powered off or unreachable, Antigravity cannot be resumed locally. All processing is cloud-first, provider-neutral, and zero-PC-inference.
- Local execution of AI video models is strictly prohibited.

### 3. Verification & Heartbeat Protocol
When active in a future phase, an Antigravity sidecar MUST only accept envelopes that satisfy:
- **Verified Authorized Envelope**: Signed/authenticated evidence from trusted origin (`AUTHENTICATED_INTERNAL` or `ASTRA_VERIFIED`).
- **User Permission**: Explicit user authorization flag in envelope metadata.
- **Machine Heartbeat**: Machine availability heartbeat verified before lease acquisition.

### 4. Quota and Capacity Metadata Policy
- No fabricated quota percentages, model statuses, reviewer approvals, or fake thinking logs.
- When worker capacity or quota status is unverified, field values MUST be set to `"UNKNOWN"`.

## Envelope Structure & State Machine
The sidecar operates strictly on the `HandOffPacket` schema:
- `PROPOSED` -> `VERIFIED` -> `QUEUED` -> `LEASED` -> `CHECKPOINTED` -> `COMPLETED`
- Holding/Error states: `BLOCKED`, `PAUSED_QUOTA`, `FAILED`, `CANCELLED`

### Fail-Closed Rejection Conditions
1. Path traversal attempts (`..`, absolute paths, symlinks pointing outside workspace).
2. Attempts to modify forbidden paths (`control_center/web/**`, `control_center/collectors/**`, `.github/workflows/**`).
3. Stale base commit SHA (`expected_base_sha` mismatch).
4. Expired lease timestamp (`lease_epoch_expiry.expires_at_utc`).
5. Duplicate or replayed receipt IDs (`idempotent_receipt_id`).
6. Active lease collisions held by a different owner.
7. Unmet task dependencies.
8. Unauthenticated Astra phase approval claims.
