# Antigravity Sidecar GitHub Command & Status Bridge

Owner-approved bridge development (Issue #44 subtask) allowing supervision of Windows Antigravity via GitHub issues using Antigravity 2.0 local sidecars and `agentapi send-message <conversation_id>`.

> **Disclaimer**: ChatGPT does NOT have a direct Google Remote Control API. The sidecar is NOT installed or enabled automatically. The user must manually inspect code and authorize local installation after security review.

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
Official Antigravity sidecars are discovered per-sidecar in:
- Windows: `%USERPROFILE%\.gemini\config\sidecars\<sidecar_id>\sidecar.json` (or `~/.gemini/config/sidecars/<sidecar_id>/sidecar.json`)

To install the bridge as an approved sidecar:
1. Create a dedicated sidecar folder: `~/.gemini/config/sidecars/github_bridge/`.
2. Copy `control_center/remote_bridge/sidecar.json.example` to `~/.gemini/config/sidecars/github_bridge/sidecar.json`.
3. Explicitly enable the sidecar in global config `~/.gemini/config/config.json`:
```json
{
  "sidecars": {
    "github_bridge": {
      "enabled": true
    }
  }
}
```

> **Working Directory Context**: Sidecars run with their current working directory (`cwd`) set to the sidecar's own folder (`~/.gemini/config/sidecars/github_bridge`), **not** the git repository folder. Therefore, the bridge configuration must explicitly specify the `workspace_dir`.

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

Both `dispatch_enabled` and `status_enabled` must be explicitly set to `true` to opt-in. If this configuration is missing, malformed, or invalid, the bridge fails closed and halts execution.

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
- Or remove/disable the sidecar entry in `~/.gemini/config/config.json`.

## Technical & API Limitations
1. **`send-message` Support Only**: The Antigravity local CLI / sidecar interface officially supports `agentapi send-message <conversation_id>`. Do not call undocumented flags like `agentapi --version`.
2. **Telemetry & Privacy Sanitization**: Status receipts posted to Issue #55 reflect local Git HEAD SHA, dirty status, and fixed enum status codes. Antigravity execution state, model, quota, and Astra review details are reported as `UNKNOWN` to avoid leaking private thoughts, reasoning, or unverified claims. Raw stderr, local user paths, tokens, and conversation IDs are never serialized.
3. **Throttling**: Status receipt postings are strictly rate-limited to at most one per 15 minutes (`900` seconds), even if a status request command specifies `force=True`.
4. **At-Most-Once Claiming**: Command claims are recorded atomically on disk using `O_EXCL` flags before `agentapi send-message` is invoked. Path traversal in command IDs is rejected. In the event of a crash or error, claims fail closed (`FAILED`) and will never automatically retry or dispatch twice.
