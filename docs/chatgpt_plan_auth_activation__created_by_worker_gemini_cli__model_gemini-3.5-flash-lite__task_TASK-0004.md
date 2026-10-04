# Sign in with ChatGPT Live Adapter & Authorization Guide (TASK-0004)

## Overview

TASK-0004 implements the live Sign in with ChatGPT adapter and one-time Windows authorization CLI (`chatgpt_plan_auth_cli`) that will later activate the Astra reviewer bridge.

To preserve safety and zero-spend constraints, all CI pipelines remain offline and do not perform real browser authentication or live OpenAI API requests.

## Architecture

- **Host Identity (`ext_agent_host_id`)**: A stable host identifier stored in host-local protected storage (`~/.openai/chatgpt_reviewer_session.json`) and reused across sign-ins.
- **PKCE S256 & OAuth**: Generates cryptographically secure PKCE verifiers, challenges, state, and nonces for the open-source Sign in with ChatGPT flow with loopback redirect on `127.0.0.1`.
- **Protected Storage**: Atomic file replacement and host-local isolation keep tokens outside the public repository.
- **Exact Model Enforcement**: Validates authorized model catalogs against exact `gpt-6-astra`.
- **Stateless Responses API**: Enforces `store=false` and `stream=true` without leaking conversation history.
- **TASK-0003 Integration**: Routes validated live reviewer payloads directly into the existing TASK-0003 immutable snapshot/verdict evaluation gate.

## CLI Usage (Post-Merge Windows Setup)

After merge, a Windows self-hosted reviewer machine can initialize host state and perform one-time interactive authorization:

```bash
# Initialize stable host ID
python scripts/chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py init-host

# Check credential-free session status
python scripts/chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py status

# Validate model catalog
python scripts/chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py models

# Run component self-check
python scripts/chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py self-check
```

Interactive sign-in (`sign-in`) requires direct user action in a browser and is blocked automatically in non-interactive CI environments.
