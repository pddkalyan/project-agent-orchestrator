# Movie Studio Control Center - Collectors

This directory contains zero-cost read-only adapters and normalized event contracts for collecting public repository activity and local task state for dashboard consumption.

## Component Overview

- `github_collector.py`: `GitHubPublicActivityCollector` class for querying public GitHub Actions runs, workflow jobs, pull requests, commit links, and local task queue / memory state files.
- `event_schema.json`: Formal JSON Schema contract defining normalized dashboard events and data envelope.
- `contract.json`: Sample output payload matching `event_schema.json`.

## Features

1. **Zero-Cost Read-Only Operation**:
   - Queries GitHub public endpoints without credentials or API tokens.
   - Strictly enforces ₹0 spending boundaries and no workflow activation or paid API calls.

2. **Resilience & Edge Case Handling**:
   - **Pagination**: Supports link-header based pagination for GitHub REST endpoints.
   - **Transient Retries**: Implements exponential backoff for HTTP 5xx errors.
   - **Rate Limiting**: Tracks `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers.
   - **Missing Data**: Soft fallbacks ensure missing payload fields render as `unknown` or `null` without crashing.

3. **Honest Field Attribution**:
   - Model, quota, thinking logs, and activity telemetry fields are set to `"unknown"` or `null` unless authoritative source fields explicitly exist in local task/state records.

4. **Integration Contract for Antigravity UI**:
   - Antigravity UI can consume payloads compliant with `event_schema.json` via HTTP GET or static artifact ingestion.
