# ChatGPT ↔ Windows Antigravity: verified connection and one-time setup gates

**Status (2026-10-10): STATUS CHANNEL WORKS; NATIVE MESSAGE DISPATCH NOT ACTIVATED.**

## What is already operational

- Repository: `pddkalyan/project-agent-orchestrator`; Windows bridge on `agent/control-center-standalone` at `15b148cfbc3acc0eec10c51ab8e8bf413613f23f`.
- ChatGPT posted a read-only `status_request` to GitHub Issue #54:
  https://github.com/pddkalyan/project-agent-orchestrator/issues/54#issuecomment-6100411763
- Windows returned `STATUS_REQUEST_PROCESSED` at 2026-10-10T17:48:39Z on Issue #55:
  https://github.com/pddkalyan/project-agent-orchestrator/issues/55#issuecomment-6100432291
- Therefore **GitHub -> Windows sidecar status messaging** has end-to-end evidence. This is not a native Antigravity `agentapi send-message` acknowledgement. The installed status bridge should **not be overwritten**.

## Official interfaces

Google's public Antigravity Sidecars documentation:
https://antigravity.google/docs/sidecars

Documents `agentapi new-conversation <prompt>` and `agentapi send-message <conversation_id> <prompt>` inside sidecars, **but does not document** `agentapi get-conversation-metadata`. The current PR #74 safe ping candidate assumes that metadata subcommand and fails closed when unavailable. Do **not** bypass it, guess response fields, or install PR #71 (explicit P0 security hold).

Google's separate browser Remote Control UI:
https://antigravity.google/docs/remote-control/
In Antigravity 2.0: Settings > App > Enable Remote Control. Access the device with the same Google account at https://antigravity.google.com/. This official browser feature does **not** automatically give this ChatGPT session a direct Antigravity API or access to the user's Windows filesystem.

## Zero-write Windows preflight (new)

The file `antigravity_bridge_preflight__created_by_chatgpt__model_gpt6.py` in this directory is **read only**. It never calls `agentapi`, changes Git/files, installs Python dependencies, alters permissions, or prints the private conversation ID.

Run once on the Windows machine, using its existing repository directory:

```powershell
python "antigravity_bridge_preflight__created_by_chatgpt__model_gpt6.py" --workspace "D:\Documents\Movie-Studio-Control-Center-Agent"
```

This should be run from the directory holding the script, or provide the absolute path to the downloaded script. If the project workspace moved, change **only** the `--workspace` argument. It checks:
- expected GitHub origin, branch and head SHA (all exact);
- installed sidecar script and manifest;
- local status-only opt-in and disabled general dispatch / ping;
- official Antigravity sidecar enablement;
- Python version, local `gh` authentication and exact GitHub owner numeric ID;
- reports dirty working tree without resetting, staging, overwriting, or deleting changes.

Expected result: status-channel requirements may show PASS, while `native_conversation_identity` is intentionally UNVERIFIED and `safe_for_native_activation` is always false. Exit 2 means at least one local prerequisite is blocked; no correction is attempted.

Run isolated tests from repository root:
```powershell
python -m unittest discover -s tests -p "test_antigravity_bridge_preflight__created_by_chatgpt__model_gpt6.py" -v
python -m py_compile "control_center/remote_bridge/antigravity_bridge_preflight__created_by_chatgpt__model_gpt6.py"
```

The tests use temporary fake configuration files and mocked commands. No real GitHub CLI, local Antigravity, or live message invocation occurs.

## Native ping activation: mandatory review and user-side steps

1. Preserve current working Windows status bridge, local uncommitted frontend files, permissions and configuration. No repository reset/rebase/force push.
2. Have PR #74 at source `1123e6ef988d18155c42257c54dd318e3f8aac2b` independently security reviewed. Its exact-SHA 38 bridge plus 3 metadata-schema tests are pending native validation. PR #71 at `e5d6ceee8f37fa369104bc3a31ed1045a6e5d213` remains **DO NOT INSTALL**.
3. On the Windows host **read-only**, establish the actual supported Antigravity native conversation identity interface. The proposed `get-conversation-metadata` is undocumented in Google's public reference; a failure/missing subcommand is **BLOCKED**, not approval to skip identity validation.
4. Correct the exact-target verification design in a new isolated tested candidate if the documented native interface cannot confirm a conversation; obtain review before installation. A fixed-message one-time benign handshake, if considered, also requires a separate user-approved scope review.
5. Only after all guards and exact candidate tests pass, request **one-time local Windows** permission to back up the current installed bridge, copy the pinned approved source, opt in `ping_enabled=true` and `status_enabled=true`, retain `dispatch_enabled=false`, and reload the sidecar. Do not install by an untrusted public GitHub comment.
6. Send one **bounded, owner-authenticated** `ping_conversation` to Issue #54. Verify BOTH `PING_DISPATCHED` or `PING_FAILED` in Issue #55 and an independent acknowledgement in the intended native Antigravity conversation. A CLI exit code alone does not establish receipt.
7. Permit any `continue_issue` coding messages **only after** a second explicit local opt-in and separate review of exact branch/commit checks, fixed prompt allowlist, clean/dirty-tree policy, replay claims, auth, per-message receipts and zero-spend rules.

**Hard restrictions:** ₹0 additional spending; no paid APIs/credits, arbitrary shell, secret access, Phase2 crossover, automatic PR merge, host sandbox bypass, or silent enabling of native dispatch. ChatGPT GitHub permissions do not include Windows file installation privileges.
