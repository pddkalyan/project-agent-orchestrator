# Antigravity Sidecar GitHub Command & Status Bridge

Owner-approved bridge development (Issue #44 subtask) allowing supervision of Windows Antigravity via GitHub issues using Antigravity 2.0 local sidecars and `agentapi send-message <conversation_id>`.

> **Disclaimer**: ChatGPT does NOT have a direct Google Remote Control API. The sidecar is NOT installed or enabled automatically. The user must manually inspect code and authorize local installation after security review.

## Overview & Principles
- **Disabled by Default**: The bridge is purely opt-in and disabled by default (`dispatch_enabled: false`, `status_enabled: false`).
- **Strict Boolean Consent**: Config boolean values must be JSON boolean `true`/`false` types. String values like `"false"` or `"true"` and unknown JSON keys are strictly rejected.
- **No Direct Secret Storage**: Authenticated using existing local host GitHub CLI (`gh`) session. No GitHub tokens or secrets are stored in code or repository files.
- **Strict Scope & Allowlist**:
  - Repo: `pddkalyan/project-agent-orchestrator`
  - Authorized Owner User ID: `159762630`
  - Command Inbox Issue: `#54`
  - Status Receipt Sink Issue: `#55`
  - Allowed Target Issues: `#44`, `#47`
  - Expected Branch: `agent/control-center-standalone`

## Prerequisites & Installation Instructions

### 1. Python & GitHub CLI Setup
Ensure Python 3.9+ and GitHub CLI (`gh`) are installed and authenticated on the Windows host machine:
```cmd
python --version
gh auth status
```

### 2. Antigravity 2.0 Sidecar Setup & Script Installation
Official Antigravity sidecars run with working directory set to their dedicated folder:
- Windows: `%USERPROFILE%\.gemini\config\sidecars\github_bridge\`

To set up the sidecar folder and copy the pinned script (DO NOT execute automatically):
1. Create dedicated folder: `mkdir -p ~/.gemini/config/sidecars/github_bridge`
2. Copy script: `cp control_center/remote_bridge/bridge.py ~/.gemini/config/sidecars/github_bridge/bridge.py`
3. Copy configuration template `control_center/remote_bridge/sidecar.json.example` to `~/.gemini/config/sidecars/github_bridge/sidecar.json`.
4. Explicitly enable the sidecar in global config `~/.gemini/config/config.json`:
```json
{
  "sidecars": {
    "github_bridge": {
      "enabled": true
    }
  }
}
```

### 3. Local Opt-In & Configuration Setup
Specify the local workspace path and conversation ID without committing sensitive URLs or tokens.
Create `~/.antigravity_bridge_state/config.json`:
```json
{
  "workspace_dir": "D:/Documents/Movie-Studio-Control-Center-Agent",
  "dispatch_enabled": false,
  "status_enabled": false,
  "conversation_id": "YOUR_LOCAL_CONVERSATION_ID_HERE"
}
```

Both `dispatch_enabled` and `status_enabled` must be JSON booleans (`true` or `false`). String types (`"false"`, `"true"`) or unknown JSON keys will cause the bridge to fail closed.

### 4. Dry-Run Setup Verification & Unit Testing
To verify configuration, git origin, script location, and CLI tool availability without sending network messages:
```cmd
python control_center/remote_bridge/bridge.py --dry-run
```

Run PowerShell/Windows-compatible offline unit tests to verify deterministic behavior:
```powershell
python -m unittest discover -s tests -p "test_control_center_remote_bridge*.py"
```

### 5. Revocation / Disabling
To immediately disable the bridge:
- Set `"dispatch_enabled": false` and `"status_enabled": false` in `~/.antigravity_bridge_state/config.json`.
- Or remove/disable the sidecar entry in `~/.gemini/config/config.json`.

## Technical & Security Safeguards
1. **`send-message` Support Only**: The Antigravity local CLI / sidecar interface officially supports `agentapi send-message <conversation_id>`.
2. **Normalized Git Origin Verification**: Exact match required for `pddkalyan/project-agent-orchestrator.git` (HTTPS/SSH). Lookalikes, alternate owners, or suffix matches are rejected.
3. **Private Dispatch Authorization**: Dispatch requires explicit authorization context and a clean working tree (`git status --porcelain`). Replays across process restarts are blocked on disk.
4. **Periodic Heartbeat & Rate Limiting**: Idle polling loop posts an opted-in periodic status receipt every 15 minutes (`<=1/15min`). Status receipts are sanitized to report execution/model as `UNKNOWN`.
5. **No Direct Writes / Merges**: The bridge script NEVER grants host permissions or performs direct Git writes, merges, or force pushes.
