# TASK-0004 — Sign in with ChatGPT live adapter and Windows authorization CLI

## Scope

TASK-0004 implements the live-capable adapter and one-time Windows CLI needed to connect the already-approved TASK-0003 reviewer bridge to an explicitly authorized ChatGPT account.

The code is safe to review and merge **without performing real authorization**. CI remains offline. The user performs browser consent only after merge.

## Current OpenAI OSS SIWC flow implemented here

For first-time registration:

- authorization endpoint: `https://auth.openai.com/api/accounts/authorize`
- `client_id=dynamic_agent_client`
- stable host identifier: `ext_agent_host_id=urn:uuid:...`
- `agent_name_hint=Project Agent Orchestrator`
- loopback redirect: `http://127.0.0.1:<port>/auth/callback`
- fresh state, nonce, PKCE verifier and S256 challenge
- scopes: `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`
- resource: `https://api.openai.com/v1`

The successful new-registration callback must return an issued `client_id` such as `oaiapp_...`. The adapter rejects a missing issued ID and never uses `dynamic_agent_client` for token exchange.

Token exchange and refresh use:

`https://auth.openai.com/api/accounts/oauth/token`

No client secret, partner API key, or `OPENAI_API_KEY` path exists.

For returning authorization, the saved issued client ID is reused with the same host ID; retained `id_token_hint` and email `login_hint` are optional account hints.

## Identity validation

The adapter requires cryptographic ID-token signature verification against OpenAI JWKS:

`https://auth.openai.com/.well-known/jwks.json`

It then validates exact issuer, issued client ID audience, saved nonce, subject, and expiry.

The Windows CLI uses a live `PyJWT[crypto]` verifier. Install that runtime dependency before the one-time sign-in:

```powershell
py -m pip install "PyJWT[crypto]"
```

Offline tests inject a fake signature verifier and make no network request.

## Credential storage

The Windows target uses DPAPI to protect `access_token`, `refresh_token`, and retained `id_token` before writing the profile file.

Default files live outside the repository under:

`%USERPROFILE%\.project-agent-orchestrator\chatgpt\`

The profile is atomically replaced. Refresh operations take a process/file lock so rotating refresh tokens are not raced. Status output never prints raw credentials.

## Model discovery

After sign-in, the adapter calls the public account-authorized model catalog and keeps visible model `slug` values. Only exact:

`gpt-6-astra`

qualifies for the project reviewer. Similar names do not qualify.

## Responses request boundary

Future live reviewer inference uses the public Responses API only:

`POST https://api.openai.com/v1/responses`

The request builder requires:

- `store=false`
- `stream=true`
- complete immutable review context carried explicitly in input
- no `previous_response_id`
- no Responses conversation state
- no background mode
- no unsupported stateful/service-tier overrides

Inference is successful only after `response.completed`. Usage-limit/auth/infrastructure failures become BLOCKED states, not APPROVED/REJECTED code-review verdicts.

## Windows post-merge setup

After TASK-0004 receives Astra approval and is merged:

```powershell
cd D:\Documents\project-agent-orchestrator
git pull

py -m pip install "PyJWT[crypto]"

py scripts\chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py init-host

py scripts\chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py sign-in

py scripts\chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py status

py scripts\chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py models
```

The `sign-in` command opens the system browser and requires direct user consent. It is blocked in CI/non-interactive shells.

## Zero-extra-spend gate

TASK-0004 does not enable automatic reviewer inference. Sign-in and model discovery are separate from the later activation step.

Before automated Astra requests are enabled, the separate activation task must confirm the user's ChatGPT app usage/credit controls are configured so the project does not intentionally consume separately billed credits. There is no API-key or paid fallback in this code.

## Conversation boundary

This integration does not read or expose existing ChatGPT conversations or ChatGPT memory. Reviewer continuity comes only from repository context and review packets.

## Current test/review status

The candidate contains 41 deterministic offline tests. TASK-0004 intentionally does not edit GitHub workflow files, so the existing repository regressions do not execute this new test module. GPT-6 Astra should run the test module locally on the exact PR head during final review.

No live browser sign-in or OpenAI inference has been performed as part of TASK-0004 development.
