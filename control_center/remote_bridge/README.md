# Antigravity ChatGPT GitHub Sidecar — Ping-Only Recovery

**Review candidate. Not installed; native delivery is not yet verified.**
The existing Windows status-only sidecar reads authenticated GitHub Issue #54
commands and writes rate-limited receipts to Issue #55. General native dispatch
must remain disabled.

## Pinned identity and source

- Owner: pddkalyan, numeric user ID 159762630.
- Repository: pddkalyan/project-agent-orchestrator.
- Target Windows worktree branch: agent/control-center-standalone.
- Exact last verified target HEAD: 15b148cfbc3acc0eec10c51ab8e8bf413613f23f.
- Exact safe source commit (PR #74): 1123e6ef988d18155c42257c54dd318e3f8aac2b.
- Exact reviewed bridge.py Git blob: adc3ac6c9c7156a1f135caaedb87fd89c9284b5e.
- Existing sidecar path: %USERPROFILE%\.gemini\config\sidecars\github_bridge\
- Private config: %USERPROFILE%\.antigravity_bridge_state\config.json
- Native conversation ID stays strictly private on Windows.
- Keep dispatch_enabled=false, allow_dirty_continue=false, status_enabled=true.
- Keep ping_enabled=false until explicit local opt-in.
- Only the existing prewritten, non-coding Issue #47 ping is eligible.

## Read-only preflight (after security review)

Run this script **from a separate, clean checkout containing this reviewed
installer and the pinned bridge.py**. Do not check out another branch inside
the user's dirty Control Center worktree.

    python control_center\remote_bridge\bootstrap_antigravity_bridge.py --workspace "D:\Documents\Movie-Studio-Control-Center-Agent" --defer-metadata-to-sidecar

Expected: READY_TO_INSTALL_READ_ONLY. This does NOT install, create any state,
register a sidecar, or send any message. It checks exact remote, branch and HEAD,
the authenticated owner, pinned source, existing registered github_bridge sidecar
and existing privately bound config. It never touches report.md/report_final.md.

## Opt-in installation only after reviewed preflight

Explicit Windows operator consent is required. Installation replaces ONLY the
script in the existing registered github_bridge directory and the private
config; it makes unique local backups and uses atomic file replacement.
It does NOT register the second antigravity_bridge sidecar.

    python control_center\remote_bridge\bootstrap_antigravity_bridge.py --workspace "D:\Documents\Movie-Studio-Control-Center-Agent" --defer-metadata-to-sidecar --activate-ping

Expected: PING_ONLY_INSTALLED_RELOAD_REQUIRED. No native message has been sent.
Reload the one known existing sidecar only after active work has finished.
If rollback fails, inspect private backups and do not send the ping.

Installer refuses changed SHA, unknown or insecure Git remote, different branch,
corrupt private config, missing bound conversation, broad dispatch permission,
conflicting sidecars and unexpected sidecar manifest.

Source resolution: verify local bridge.py against exact Git blob first. If local
source is missing, fetch through authenticated gh api at the exact reviewed
commit; never use default-branch content or raw.githubusercontent.com fallbacks.
Transient gh child-process errors receive bounded retries with redacted,
non-secret error classifications. Corrupted source fails closed.

## Test and acceptance gates

From the isolated candidate root, run:

    python -m unittest discover -s tests -p "test_bootstrap_antigravity_bridge.py" -v
    python -m unittest discover -s tests -p "test_control_center_remote_bridge.py" -v
    python -m unittest discover -s tests -p "test_bridge_metadata_schema__created_by_agent_gpt6.py" -v
    python -m py_compile control_center/remote_bridge/bootstrap_antigravity_bridge.py control_center/remote_bridge/bridge.py
    git diff --check

Container regression tests are **not** a substitute for native Windows
verification: the installer was not run on the user's host. The reviewed
bridge must obtain a matching exact native conversation_id from authoritative
agentapi get-conversation-metadata before ANY message is dispatched.
Then require BOTH a PING_DISPATCHED Issue #55 receipt (15-minute rate limit)
and a genuine visible acknowledgment in the correct Antigravity conversation.

Do not merge automatically, force-push, spend money, delete local files,
weaken metadata checks, or turn on unrestricted remote command execution.
