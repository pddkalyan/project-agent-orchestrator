"""JSON Schema definition and validator for Control Center Task Handoff Packets."""

import re

HANDOFF_PACKET_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ControlCenterHandoffPacket",
    "type": "object",
    "required": [
        "task_id",
        "objective_type",
        "repository",
        "expected_base_sha",
        "requested_agent",
        "proposed_files_allowlist",
        "source_review_evidence_refs",
        "dependencies",
        "origin_trust_type",
        "lease_epoch_expiry",
        "idempotent_receipt_id",
        "checkpoint_last_tested_sha",
        "status_error",
        "retry_counter",
    ],
    "properties": {
        "task_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "objective_type": {"type": "string", "minLength": 1, "maxLength": 64},
        "repository": {"type": "string", "minLength": 1, "maxLength": 128},
        "expected_base_sha": {"type": "string", "pattern": "^[0-9a-fA-F]{40}$"},
        "requested_agent": {"type": "string", "minLength": 1, "maxLength": 64},
        "proposed_files_allowlist": {
            "type": "array",
            "items": {"type": "string"},
        },
        "source_review_evidence_refs": {"type": "object"},
        "dependencies": {
            "type": "array",
            "items": {"type": "string"},
        },
        "origin_trust_type": {
            "type": "string",
            "enum": [
                "UNTRUSTED_PROPOSAL",
                "AUTHENTICATED_INTERNAL",
                "ASTRA_VERIFIED",
            ],
        },
        "lease_epoch_expiry": {
            "type": "object",
            "required": ["lease_owner", "epoch", "expires_at_utc"],
            "properties": {
                "lease_owner": {"type": ["string", "null"]},
                "epoch": {"type": "integer", "minimum": 0},
                "expires_at_utc": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "idempotent_receipt_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "checkpoint_last_tested_sha": {
            "type": ["string", "null"],
            "pattern": "^([0-9a-fA-F]{40})?$",
        },
        "status_error": {
            "type": "object",
            "required": ["status", "error_code", "error_message"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "PROPOSED",
                        "VERIFIED",
                        "QUEUED",
                        "LEASED",
                        "CHECKPOINTED",
                        "COMPLETED",
                        "BLOCKED",
                        "PAUSED_QUOTA",
                        "FAILED",
                        "CANCELLED",
                    ],
                },
                "error_code": {"type": ["string", "null"]},
                "error_message": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "retry_counter": {"type": "integer", "minimum": 0},
        "metadata": {"type": "object"},
    },
    "additionalProperties": False,
}

FORBIDDEN_PATH_PATTERNS = [
    r"^control_center/web(/|$)",
    r"^control_center/collectors(/|$)",
    r"^\.github/workflows(/|$)",
    r"^\.git(/|$)",
    r"^/etc(/|$)",
    r"^/sys(/|$)",
    r"^/proc(/|$)",
    r"^C:\\",
]


def validate_packet_dict(data: dict) -> tuple[bool, str]:
    """Validate python dictionary against handoff packet schema rules deterministically.

    Returns (is_valid, error_reason).
    """
    if not isinstance(data, dict):
        return False, "Packet root must be a JSON object"

    required_keys = HANDOFF_PACKET_SCHEMA["required"]
    for key in required_keys:
        if key not in data:
            return False, f"Missing required field: {key}"

    # Check for unknown top-level keys
    allowed_keys = set(HANDOFF_PACKET_SCHEMA["properties"].keys())
    for key in data.keys():
        if key not in allowed_keys:
            return False, f"Unknown property: {key}"

    # task_id
    if not isinstance(data["task_id"], str) or not data["task_id"].strip():
        return False, "task_id must be a non-empty string"

    # objective_type
    if not isinstance(data["objective_type"], str) or not data["objective_type"].strip():
        return False, "objective_type must be a non-empty string"

    # repository
    if not isinstance(data["repository"], str) or not data["repository"].strip():
        return False, "repository must be a non-empty string"

    # expected_base_sha
    sha = data["expected_base_sha"]
    if not isinstance(sha, str) or not re.match(r"^[0-9a-fA-F]{40}$", sha):
        return False, f"expected_base_sha must be a 40-character hex string, got: {sha}"

    # requested_agent
    if not isinstance(data["requested_agent"], str) or not data["requested_agent"].strip():
        return False, "requested_agent must be a non-empty string"

    # proposed_files_allowlist
    allowlist = data["proposed_files_allowlist"]
    if not isinstance(allowlist, list):
        return False, "proposed_files_allowlist must be a list of file path strings"
    for item in allowlist:
        if not isinstance(item, str):
            return False, "proposed_files_allowlist entries must be strings"

    # source_review_evidence_refs
    if not isinstance(data["source_review_evidence_refs"], dict):
        return False, "source_review_evidence_refs must be an object/dict"

    # dependencies
    deps = data["dependencies"]
    if not isinstance(deps, list):
        return False, "dependencies must be a list of task ID strings"
    for dep in deps:
        if not isinstance(dep, str):
            return False, "dependency entries must be strings"

    # origin_trust_type
    trust_types = HANDOFF_PACKET_SCHEMA["properties"]["origin_trust_type"]["enum"]
    if data["origin_trust_type"] not in trust_types:
        return False, f"origin_trust_type must be one of {trust_types}"

    # lease_epoch_expiry
    lease = data["lease_epoch_expiry"]
    if not isinstance(lease, dict):
        return False, "lease_epoch_expiry must be an object"
    for req in ["lease_owner", "epoch", "expires_at_utc"]:
        if req not in lease:
            return False, f"lease_epoch_expiry missing required field: {req}"
    for k in lease.keys():
        if k not in ["lease_owner", "epoch", "expires_at_utc"]:
            return False, f"lease_epoch_expiry contains invalid key: {k}"

    if lease["lease_owner"] is not None and not isinstance(lease["lease_owner"], str):
        return False, "lease_owner must be a string or null"
    if not isinstance(lease["epoch"], int) or lease["epoch"] < 0:
        return False, "epoch must be a non-negative integer"
    if lease["expires_at_utc"] is not None and not isinstance(lease["expires_at_utc"], str):
        return False, "expires_at_utc must be an ISO timestamp string or null"

    # idempotent_receipt_id
    if not isinstance(data["idempotent_receipt_id"], str) or not data["idempotent_receipt_id"].strip():
        return False, "idempotent_receipt_id must be a non-empty string"

    # checkpoint_last_tested_sha
    last_sha = data["checkpoint_last_tested_sha"]
    if last_sha is not None:
        if not isinstance(last_sha, str) or not re.match(r"^[0-9a-fA-F]{40}$", last_sha):
            return False, "checkpoint_last_tested_sha must be a 40-character hex string or null"

    # status_error
    st_err = data["status_error"]
    if not isinstance(st_err, dict):
        return False, "status_error must be an object"
    for req in ["status", "error_code", "error_message"]:
        if req not in st_err:
            return False, f"status_error missing required field: {req}"
    for k in st_err.keys():
        if k not in ["status", "error_code", "error_message"]:
            return False, f"status_error contains invalid key: {k}"

    statuses = HANDOFF_PACKET_SCHEMA["properties"]["status_error"]["properties"]["status"]["enum"]
    if st_err["status"] not in statuses:
        return False, f"status must be one of {statuses}"
    if st_err["error_code"] is not None and not isinstance(st_err["error_code"], str):
        return False, "error_code must be a string or null"
    if st_err["error_message"] is not None and not isinstance(st_err["error_message"], str):
        return False, "error_message must be a string or null"

    # retry_counter
    retry = data["retry_counter"]
    if not isinstance(retry, int) or retry < 0:
        return False, "retry_counter must be a non-negative integer"

    # metadata
    if "metadata" in data and not isinstance(data["metadata"], dict):
        return False, "metadata must be an object/dict if present"

    return True, ""
