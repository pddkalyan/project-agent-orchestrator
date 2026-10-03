# Astra Review Protocol

Astra is a reviewer, not the routine worker.

## Worker submission
A review packet should contain:
- objective
- changed artifacts
- tests/evidence
- known limitations
- previous Astra objections being addressed
- spend/quota impact
- provenance

## Astra outcomes
### APPROVED
The candidate can be promoted to the known-good baseline.

### REJECTED
Astra must return:
- concrete issue(s)
- severity
- why each issue matters
- required correction
- measurable acceptance test
- optional recommended approach

The free worker then corrects the work autonomously, reruns tests, and resubmits.

## Loop control
Do not loop indefinitely. Repeated failure on the same blocker must produce a persistent diagnosis and an escalation record rather than wasting quota.
