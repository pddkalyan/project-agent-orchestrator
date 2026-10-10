# Antigravity Sidecar GitHub Command & Status Bridge

Owner-approved bridge development (Issue #44 subtask) allowing supervision of Windows Antigravity via GitHub issues using Antigravity 2.0 local sidecars and `agentapi send-message <conversation_id>`.

## Overview & Principles
- **Disabled by Default**: The bridge is purely opt-in and disabled by default (`dispatch_enabled: false`, `status_enabled: false`).
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

### 2. Antigravity 2.0 Sidecar Discovery & Configuration
Official Antigravity sidecars are discovered in:
- Windows: `%USERPROFILE%\.gemini\config\sidecars\` (or `~/.gemini/config/sidecars/`)

To install the bridge as an approved sidecar:
1. Copy `control_center/remote_bridge/sidecar.json.example` to `~/.gemini/config/sidecars/github_bridge.json`.
2. Explicitly enable the sidecar in `~/.gemini/config/config.json`:
```json
{
  "sidecars": {
    "github_bridge": {
      "enabled": true
    }
  }
}
```

### 3. Local Opt-In & Conversation ID Setup
Specify the conversation ID locally without committing sensitive URLs or tokens.
Create or update `~/.antigravity_bridge_state/config.json`:
```json
{
  "dispatch_enabled": true,
  "status_enabled": true,
  "conversation_id": "YOUR_LOCAL_CONVERSATION_ID_HERE"
}
```

### 4. Dry-Run & Offline Testing
Run local unit tests to verify the bridge logic deterministically without network calls:
```cmd
PYTHONPATH=. python -m unittest -v tests/test_control_center_remote_bridge.py
```

To test local polling manually in dry-run mode:
```cmd
python control_center/remote_bridge/bridge.py
```

### 5. Revocation / Disabling
To immediately disable the bridge:
- Set `"dispatch_enabled": false` and `"status_enabled": false` in `~/.antigravity_bridge_state/config.json`.
- Or remove the sidecar entry from `~/.gemini/config/config.json`.

## Technical & API Limitations
1. **`send-message` Support Only**: The Antigravity local CLI / sidecar interface officially supports `agentapi send-message <conversation_id>`. There is no official documented `get-status` or live inspection API.
2. **Telemetry Sanitization**: Status receipts posted to Issue #55 reflect local Git status, HEAD SHA, branch, and bridge process state. Antigravity execution state, model, quota, and Astra review details are reported as `UNKNOWN` to avoid leaking private thoughts, reasoning, or unverified claims.
3. **PC-Off Behavior**: The bridge runs locally as a sidecar process. If the host machine is turned off or offline, polling stops and commands will expire.
4. **At-Most-Once Claiming**: Command claims are recorded atomically on disk before `agentapi send-message` is invoked. In the event of a crash or timeout, claims fail closed (`FAILED`) and will never automatically retry or dispatch twice.
