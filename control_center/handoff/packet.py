"""HandoffPacket implementation with strict fail-closed validation and state machine logic."""

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from control_center.handoff.schema import FORBIDDEN_PATH_PATTERNS, validate_packet_dict


class ValidationError(Exception):
    """Raised when handoff packet fail-closed validation fails."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class StateTransitionError(Exception):
    """Raised when an illegal state machine transition is attempted."""

    def __init__(self, from_state: str, to_state: str, message: str):
        super().__init__(f"Illegal state transition from '{from_state}' to '{to_state}': {message}")
        self.from_state = from_state
        self.to_state = to_state
        self.message = message


# Legal state transitions map
LEGAL_TRANSITIONS: Dict[str, set[str]] = {
    "PROPOSED": {"VERIFIED", "BLOCKED", "FAILED", "CANCELLED"},
    "VERIFIED": {"QUEUED", "BLOCKED", "CANCELLED", "FAILED"},
    "QUEUED": {"LEASED", "PAUSED_QUOTA", "CANCELLED", "FAILED"},
    "LEASED": {"CHECKPOINTED", "COMPLETED", "PAUSED_QUOTA", "FAILED", "CANCELLED"},
    "CHECKPOINTED": {"LEASED", "COMPLETED", "PAUSED_QUOTA", "FAILED", "CANCELLED"},
    "PAUSED_QUOTA": {"QUEUED", "LEASED", "CANCELLED", "FAILED"},
    "BLOCKED": {"PROPOSED", "CANCELLED", "FAILED"},
    "FAILED": set(),
    "COMPLETED": set(),
    "CANCELLED": set(),
}

MAX_RETRIES = 5


class HandOffPacket:
    """Representation of a Control Center Task Handoff Packet."""

    def __init__(
        self,
        task_id: str,
        objective_type: str,
        repository: str,
        expected_base_sha: str,
        requested_agent: str,
        proposed_files_allowlist: List[str],
        source_review_evidence_refs: Dict[str, Any],
        dependencies: List[str],
        origin_trust_type: str = "UNTRUSTED_PROPOSAL",
        lease_owner: Optional[str] = None,
        lease_epoch: int = 0,
        lease_expires_at_utc: Optional[str] = None,
        idempotent_receipt_id: Optional[str] = None,
        checkpoint_last_tested_sha: Optional[str] = None,
        status: str = "PROPOSED",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_counter: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.task_id = task_id
        self.objective_type = objective_type
        self.repository = repository
        self.expected_base_sha = expected_base_sha
        self.requested_agent = requested_agent
        self.proposed_files_allowlist = list(proposed_files_allowlist)
        self.source_review_evidence_refs = dict(source_review_evidence_refs)
        self.dependencies = list(dependencies)
        self.origin_trust_type = origin_trust_type

        self.lease_owner = lease_owner
        self.lease_epoch = lease_epoch
        self.lease_expires_at_utc = lease_expires_at_utc

        self.idempotent_receipt_id = idempotent_receipt_id or f"rcpt_{task_id}_{lease_epoch}"
        self.checkpoint_last_tested_sha = checkpoint_last_tested_sha

        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.retry_counter = retry_counter
        self.metadata = dict(metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        """Convert packet to JSON-serializable dictionary."""
        return {
            "task_id": self.task_id,
            "objective_type": self.objective_type,
            "repository": self.repository,
            "expected_base_sha": self.expected_base_sha,
            "requested_agent": self.requested_agent,
            "proposed_files_allowlist": self.proposed_files_allowlist,
            "source_review_evidence_refs": self.source_review_evidence_refs,
            "dependencies": self.dependencies,
            "origin_trust_type": self.origin_trust_type,
            "lease_epoch_expiry": {
                "lease_owner": self.lease_owner,
                "epoch": self.lease_epoch,
                "expires_at_utc": self.lease_expires_at_utc,
            },
            "idempotent_receipt_id": self.idempotent_receipt_id,
            "checkpoint_last_tested_sha": self.checkpoint_last_tested_sha,
            "status_error": {
                "status": self.status,
                "error_code": self.error_code,
                "error_message": self.error_message,
            },
            "retry_counter": self.retry_counter,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HandOffPacket":
        """Construct HandOffPacket from dictionary with fail-closed schema validation."""
        valid, reason = validate_packet_dict(data)
        if not valid:
            raise ValidationError("MALFORMED_SCHEMA", reason)

        lease_info = data["lease_epoch_expiry"]
        st_err = data["status_error"]

        return cls(
            task_id=data["task_id"],
            objective_type=data["objective_type"],
            repository=data["repository"],
            expected_base_sha=data["expected_base_sha"],
            requested_agent=data["requested_agent"],
            proposed_files_allowlist=data["proposed_files_allowlist"],
            source_review_evidence_refs=data["source_review_evidence_refs"],
            dependencies=data["dependencies"],
            origin_trust_type=data["origin_trust_type"],
            lease_owner=lease_info["lease_owner"],
            lease_epoch=lease_info["epoch"],
            lease_expires_at_utc=lease_info["expires_at_utc"],
            idempotent_receipt_id=data["idempotent_receipt_id"],
            checkpoint_last_tested_sha=data["checkpoint_last_tested_sha"],
            status=st_err["status"],
            error_code=st_err["error_code"],
            error_message=st_err["error_message"],
            retry_counter=data["retry_counter"],
            metadata=data.get("metadata", {}),
        )

    def validate_security_and_constraints(
        self,
        current_repo_sha: Optional[str] = None,
        workspace_root: Optional[str] = None,
        completed_dependency_task_ids: Optional[set[str]] = None,
        now_utc: Optional[datetime] = None,
    ) -> None:
        """Fail-closed validation check covering file paths, base SHA, lease expiry, dependencies, and Astra claims."""
        # 1. Validate file paths in proposed_files_allowlist
        for path_str in self.proposed_files_allowlist:
            self._validate_file_path(path_str, workspace_root=workspace_root)

        # 2. Validate expected base SHA if provided
        if current_repo_sha is not None:
            if self.expected_base_sha.lower() != current_repo_sha.lower():
                raise ValidationError(
                    "STALE_BASE_SHA",
                    f"Expected base SHA '{self.expected_base_sha}' does not match current repository SHA '{current_repo_sha}'",
                )

        # 3. Validate lease expiry if expires_at_utc is set
        if self.lease_expires_at_utc:
            current_dt = now_utc or datetime.now(timezone.utc)
            try:
                # ISO format parse
                exp_str = self.lease_expires_at_utc.replace("Z", "+00:00")
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if current_dt > exp_dt:
                    raise ValidationError(
                        "EXPIRED_LEASE",
                        f"Lease expired at {self.lease_expires_at_utc} (current time: {current_dt.isoformat()})",
                    )
            except ValueError as e:
                raise ValidationError("INVALID_LEASE_EXPIRY_TIMESTAMP", f"Invalid expiry timestamp format: {e}")

        # 4. Validate dependencies
        if self.dependencies:
            completed_set = completed_dependency_task_ids or set()
            unmet = [dep for dep in self.dependencies if dep not in completed_set]
            if unmet:
                raise ValidationError(
                    "UNMET_DEPENDENCY",
                    f"Task has unmet completed dependencies: {unmet}",
                )

        # 5. Validate claims of Astra phase approval
        refs = self.source_review_evidence_refs
        has_astra_claim = (
            refs.get("astra_approved") is True
            or refs.get("astra_phase_approval") is True
            or refs.get("astra_review_status") == "APPROVED"
        )
        if has_astra_claim:
            # Requires origin_trust_type == "ASTRA_VERIFIED" and valid authenticated signature
            sig = refs.get("astra_signature")
            if self.origin_trust_type != "ASTRA_VERIFIED" or not sig or not isinstance(sig, str) or len(sig.strip()) < 8:
                raise ValidationError(
                    "UNAUTHENTICATED_ASTRA_APPROVAL",
                    "Claimed Astra phase approval without authenticated signature and trust status",
                )

    def _validate_file_path(self, path_str: str, workspace_root: Optional[str] = None) -> None:
        """Enforce strict path safety check: no traversal, no forbidden dirs, inside workspace."""
        if "\0" in path_str:
            raise ValidationError("MALICIOUS_FILE_PATH", f"Path contains null byte: {path_str}")

        # Check absolute or Windows drive paths
        if os.path.isabs(path_str) or re.match(r"^[a-zA-Z]:", path_str):
            raise ValidationError("DISALLOWED_FILE_PATH", f"Absolute paths are forbidden: {path_str}")

        # Normalize relative path representation
        normalized = os.path.normpath(path_str).replace("\\", "/")

        if normalized.startswith("..") or "/../" in f"/{normalized}/":
            raise ValidationError("PATH_TRAVERSAL_ATTEMPT", f"Path traversal attempt detected: {path_str}")

        # Forbidden paths check
        for pattern in FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                raise ValidationError("FORBIDDEN_FILE_PATH", f"Path matches forbidden policy pattern '{pattern}': {path_str}")

        # Symlink and workspace boundary check
        if workspace_root:
            abs_root = os.path.abspath(workspace_root)
            target_path = os.path.abspath(os.path.join(abs_root, normalized))

            if os.path.islink(target_path):
                real_target = os.path.realpath(target_path)
                if not real_target.startswith(abs_root):
                    raise ValidationError("SYMLINK_OUTSIDE_WORKSPACE", f"Symlink points outside workspace: {path_str}")

            if not target_path.startswith(abs_root):
                raise ValidationError("PATH_OUTSIDE_WORKSPACE", f"Path targets outside workspace: {path_str}")

    def transition_to(
        self,
        new_status: str,
        reason: str = "",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        untrusted_request: bool = False,
    ) -> None:
        """Execute state transition if legal, enforcing fail-closed rules."""
        if untrusted_request and new_status not in {"PROPOSED", "CANCELLED"}:
            raise StateTransitionError(
                self.status,
                new_status,
                "Untrusted proposals/comments cannot directly trigger state transitions",
            )

        if new_status not in LEGAL_TRANSITIONS.get(self.status, set()):
            raise StateTransitionError(
                self.status,
                new_status,
                f"Transition from {self.status} to {new_status} is not allowed by state machine",
            )

        # Retry ceiling enforcement
        if new_status == "FAILED" or error_code is not None:
            self.retry_counter += 1
            if self.retry_counter > MAX_RETRIES:
                new_status = "FAILED"
                error_code = "RETRY_LIMIT_EXCEEDED"
                error_message = f"Exceeded maximum retry limit of {MAX_RETRIES}"

        self.status = new_status
        if error_code or error_message:
            self.error_code = error_code
            self.error_message = error_message
        elif new_status in {"VERIFIED", "QUEUED", "LEASED", "CHECKPOINTED", "COMPLETED"}:
            self.error_code = None
            self.error_message = None
