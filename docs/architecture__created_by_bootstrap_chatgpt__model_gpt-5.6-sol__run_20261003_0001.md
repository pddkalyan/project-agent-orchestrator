# Cloud Agent Architecture

## Control plane
Manual or scheduled trigger -> free cloud worker -> persistent memory -> isolated cloud tools/renderers.

## Reviewer plane
Astra receives compact milestone review packets only. It returns APPROVED or a structured correction package. Rejected work goes back to the worker for autonomous correction and resubmission.

## Execution plane
Routine research, coding, package experiments, cloud rendering, testing, and benchmarking happen away from the user's PC.

## Media abstraction
Story/scene logic talks only to provider-neutral contracts:
- VideoProvider.generate/status/cancel/download/capabilities/quota
- ImageProvider
- AudioProvider

Provider adapters can later include Kaggle-hosted workflows, Hugging Face tools, Mage, or future services.

## Persistent memory
Durable state stores goals, current state, failures, successes, reviewer feedback, benchmarks, cleanup manifests, and next actions.

## Safety
PC cleanup is audit-only until exact agent ownership is established. Cloud runners should be disposable. Spend defaults to $0.
