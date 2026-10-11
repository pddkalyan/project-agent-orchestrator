# Antigravity ChatGPT GitHub Sidecar — Ping-Only Recovery

**Review candidate for enhanced coding-dispatch security. Not installed.** The previously installed **ping-only** bridge has now been verified end-to-end; this stricter draft candidate has not been deployed. Never enable coding dispatch until a separate local authorization and full review.
The existing Windows ping-enabled sidecar reads authenticated GitHub Issue #54
commands and writes rate-limited receipts to Issue #55. General native dispatch
must remain disabled.

## Pinned identity and source

- Owner: pddkalyan, numeric user ID 159762630.
- Repository: pddkalyan/project-agent-orchestrator.
- Target Windows worktree branch: agent/control-center-standalone.
- Exact last verified target HEAD: 15b148cfbc3acc0eec10c51ab8e8bf413613f23f.
- Pinned reviewed candidate source commit (this draft): af71a090d30e5247393bb02bd4317e09a9678b57.
- Exact reviewed bridge.py Git blob: c20f8d831614fb92b60a429106bfde13d363abe3.
- Existing sidecar path: %USERPROFILE%\.gemini\config\sidecars\github_bridge\
- Private config: %USERPROFILE%\.antigravity_bridge_state\config.json
- Native conversation ID stays strictly private on Windows.
- Keep dispatch_enabled=false, allow_dirty_continue=false, status_enabled=true.
- The existing host ping-only integration was locally opted in and confirmed; keep
  ping_enabled=true only where already approved. For fresh hosts, start with false.
- Only the existing prewritten, non-coding Issue #47 ping is currently eligible.
- This candidate adds **mandatory authoritative exact native metadata matching** to the separate `continue_issue` path, rejects silent code dispatch, and rechecks Git identity and dirty-tree state after metadata lookup. `dispatch_enabled` stays false on the live host.

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

## Remaining dispatch-release gates (not yet met)

- Review this draft candidate independently and run the entire installer, bridge, metadata and negative regression suites **on Windows** against the exact PR head. Do not confuse static changes with executed tests.
- The status-receipt buffer is implemented as a bounded, crash-safe FIFO queue (MAX_QUEUE_CAPACITY = 10), correlating command IDs using nonsecret truncated SHA256 hashes, requiring durable status queue capacity before sending any **coding** instruction, with migration for legacy single-slot results, and tested against failed API posts, malformed queue, saturation, and restarts.
- Require an authorized local, task-specific opt-in (fixed Issue #47 task only, no arbitrary prompts or shell) with private exact conversation metadata, explicit scope, tests, TTL and replay guard. A successful ping does not grant this permission.
- Preserve the user's dirty worktree and untracked reports. Do not merge PRs or start Phase 3 without verified phase tests and a genuine Astra final review.
