# Antigravity Sidecar GitHub Command & Status Bridge

Owner-approved bridge development (Issue #44 subtask) allowing supervision of Windows Antigravity via GitHub issues using Antigravity local sidecars and `agentapi send-message <conversation_id>`.

> **Disclaimer**: ChatGPT does NOT have a direct Google Remote Control API. The sidecar is NOT installed or enabled automatically. The user must manually inspect code and authorize local installation after security review.

## Overview & Principles
- **Disabled by Default**: The bridge is purely opt-in and disabled by default (`dispatch_enabled: false`, `status_enabled: false`). Safe initial status monitoring uses `status_enabled: true`, `dispatch_enabled: false`.
- **Strict Rate Limits & Bounds**:
  - `poll_interval_sec`: Must be an integer between 60 and 3600 seconds (default 900).
  - `min_status_interval_sec`: Must be an integer >= 900 seconds (15 minutes). No force/status request can bypass the 15-minute rate limit cap.
- **Strict Boolean Consent**: Config boolean values must be JSON boolean `true`/`false` types. String values like `"false"` or `"true"` and unknown JSON keys are strictly rejected (no boolean coercion).
- **No Direct Secret Storage**: Authenticated using existing local host GitHub CLI (`gh`) session. No GitHub tokens or secrets are stored in code or repository files.
- **Strict Scope & Allowlist**:
  - Repo: `pddkalyan/project-agent-orchestrator`
  - Authorized Owner User ID: `159762630`
  - Command Inbox Issue: `#54`
  - Status Receipt Sink Issue: `#55`
  - Allowed Target Issues: `#44`, `#47`
  - Expected Branch: `agent/control-center-standalone`

## Prerequisites & One-Time Windows Setup

### 1. Python & GitHub CLI Verification
Verify Python 3.9+ and GitHub CLI (`gh`) on the Windows host machine:
```powershell
python --version
gh auth status
```
*Note*: `gh` may NOT be installed or authenticated by default. Do NOT claim the system is ready until `gh auth status` returns 0 with an authenticated session for owner `pddkalyan`.

### 2. Sidecar Location & Installation
Official Antigravity sidecars run from their dedicated configuration folder on Windows:
`%USERPROFILE%\.gemini\config\sidecars\github_bridge\`

Windows PowerShell setup commands (run once after local security approval):
```powershell
# Create dedicated sidecar folder
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.gemini\config\sidecars\github_bridge"

# Copy sidecar script and sidecar manifest
Copy-Item "control_center\remote_bridge\bridge.py" "$env:USERPROFILE\.gemini\config\sidecars\github_bridge\bridge.py"
Copy-Item "control_center\remote_bridge\sidecar.json.example" "$env:USERPROFILE\.gemini\config\sidecars\github_bridge\sidecar.json"
```

Alternatively, native Antigravity UI settings can be used to add the sidecar without manual terminal invocation. Routine manual Git commands are NOT required once configured.

### 3. Local Opt-In Configuration Setup
Create local state directory and configuration file at `%USERPROFILE%\.antigravity_bridge_state\config.json`:
```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.antigravity_bridge_state"
```

Save initial safe configuration to `%USERPROFILE%\.antigravity_bridge_state\config.json`:
```json
{
  "workspace_dir": "C:\\path\\to\\project-agent-orchestrator",
  "dispatch_enabled": false,
  "status_enabled": true,
  "conversation_id": "conv-your-active-id",
  "poll_interval_sec": 900,
  "min_status_interval_sec": 900,
  "allow_dirty_continue": false
}
```

#### Configuration Options:
- `workspace_dir` (string): Absolute path to local repository workspace directory.
- `dispatch_enabled` (boolean): `true` to allow `continue_issue` prompts; default `false`.
- `status_enabled` (boolean): `true` to post status receipts to Issue #55; default `false`.
- `conversation_id` (string): Native active Antigravity conversation ID for `send-message`.
- `poll_interval_sec` (integer): Polling frequency in seconds (60 to 3600).
- `min_status_interval_sec` (integer): Minimum status posting interval in seconds (>= 900).
- `allow_dirty_continue` (boolean): When `true` and working tree is dirty, permits prompt continuation for existing Issue #44/#47 conversations on matching branch and SHA without shell/repo writes. Automatic new work remains blocked.

### 4. Dry-Run Setup Verification & Offline Unit Testing
To verify setup without creating GitHub comments or sending messages:
```powershell
python "$env:USERPROFILE\.gemini\config\sidecars\github_bridge\bridge.py" --dry-run
```

Run offline unit tests:
```powershell
PYTHONPATH=. python -m unittest -v tests/test_control_center_remote_bridge.py
```

### 5. Revocation & Disabling
To immediately disable the bridge:
- Set `"dispatch_enabled": false` and `"status_enabled": false` in `%USERPROFILE%\.antigravity_bridge_state\config.json`.
- Or set `"enabled": false` in `%USERPROFILE%\.gemini\config\config.json`.

## Technical & Security Safeguards
1. **`send-message` Support Only**: The Antigravity local CLI / sidecar interface officially supports `agentapi send-message <conversation_id>`.
2. **Normalized Git Origin Verification**: Exact match required for `pddkalyan/project-agent-orchestrator` (HTTPS or canonical `git@github.com:` SSH). HTTP, embedded URL credentials, or lookalike hosts (e.g. `github.com.attacker.com`) are rejected.
3. **Private Dispatch Authorization**: Dispatch requires explicit authorization context and matching branch and HEAD SHA.
4. **Dirty Working Tree Protection**: Automatic new work is blocked while dirty. Prompt continuation is permitted only if `allow_dirty_continue: true` is explicitly opted-in for the same existing conversation and allowlisted task.
5. **Periodic Heartbeat & Rate Limiting**: Status posting is strictly capped to at most once per 15 minutes (`min_status_interval_sec >= 900`). Public status receipts omit raw paths, stderr, tokens, or prompts, reporting execution status as `UNKNOWN`.
6. **No Direct Writes / Merges**: The bridge script NEVER performs direct Git writes, merges, or force pushes.
