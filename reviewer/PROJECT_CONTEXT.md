# Project Context: Autonomous Cloud Cinematic R&D System

## Overview
Project Agent Orchestrator builds and operates a cloud-first cinematic R&D system.
- **Workers:** Free cloud worker agents handle routine research, code generation, testing, and cloud orchestration.
- **Reviewer:** GPT-6 Astra acts as the sparse reviewer and supervisor via a zero-cost bridge scaffold.
- **Video Generation:** Cloud-only inference via provider adapters; zero local AI video generation.
- **Cost Policy:** Strict zero spend ($0) default; no paid API billing or credit purchases.
- **Cleanup Policy:** Audit-only PC cleanup; no arbitrary local file removal.

## Reviewer Bridge Purpose
The ChatGPT plan reviewer bridge enables offline and self-hosted review of exact PR SHAs using account-authorized model access (when available via user setup), enforcing strict safety boundaries without storing secrets or executing merges.
