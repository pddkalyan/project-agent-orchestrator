"""Atomic local JSON checkpoint store with corruption recovery and lock management."""

import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from control_center.handoff.packet import HandOffPacket, StateTransitionError, ValidationError


class CheckpointStoreError(Exception):
    """Raised for persistence, checksum, or store recovery errors."""
    pass


class CheckpointStore:
    """Atomic local file checkpoint store for durable task handoff packets."""

    def __init__(self, store_dir: str):
        self.store_dir = os.path.abspath(store_dir)
        os.makedirs(self.store_dir, exist_ok=True)
        self.receipts_file = os.path.join(self.store_dir, "receipt_registry.json")
        self.lock_file = os.path.join(self.store_dir, ".store.lock")

    @contextlib.contextmanager
    def _lock_store(self, timeout_seconds: float = 10.0):
        """Process-safe advisory lock using fcntl.flock to guarantee atomic CAS and safe lease acquisition."""
        lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        start_time = time.time()
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.time() - start_time > timeout_seconds:
                        raise CheckpointStoreError("Failed to acquire exclusive store lock within timeout")
                    time.sleep(0.01)
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(lock_fd)

    def _get_packet_path(self, task_id: str) -> str:
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        return os.path.join(self.store_dir, f"packet_{safe_id}.json")

    def save_packet(self, packet: HandOffPacket) -> str:
        """Atomic save of a HandOffPacket to local JSON file with SHA-256 checksum and atomic receipt registration."""
        with self._lock_store():
            return self._save_packet_unlocked(packet)

    def _save_packet_unlocked(self, packet: HandOffPacket) -> str:
        # Pre-check receipt ID collision BEFORE modifying or creating any file
        registry = self._load_receipts_unlocked()
        receipt_id = packet.idempotent_receipt_id
        if receipt_id in registry and registry[receipt_id] != packet.task_id:
            raise CheckpointStoreError(
                f"Duplicate receipt ID collision: '{receipt_id}' already registered for task '{registry[receipt_id]}'"
            )

        data = packet.to_dict()
        packet_json = json.dumps(data, indent=2, sort_keys=True)
        checksum = hashlib.sha256(packet_json.encode("utf-8")).hexdigest()

        record = {
            "checksum": checksum,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "packet": data,
        }
        record_json = json.dumps(record, indent=2, sort_keys=True)

        target_path = self._get_packet_path(packet.task_id)
        temp_pkt_fd, temp_pkt_path = tempfile.mkstemp(dir=self.store_dir, prefix="tmp_pkt_")

        # Prepare updated registry
        registry[receipt_id] = packet.task_id
        temp_rcpt_fd, temp_rcpt_path = tempfile.mkstemp(dir=self.store_dir, prefix="tmp_rcpt_")

        try:
            # Write packet temp file
            with os.fdopen(temp_pkt_fd, "w", encoding="utf-8") as f:
                f.write(record_json)
                f.flush()
                os.fsync(f.fileno())

            # Write receipt registry temp file
            with os.fdopen(temp_rcpt_fd, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replacements
            os.replace(temp_pkt_path, target_path)
            os.replace(temp_rcpt_path, self.receipts_file)

        except Exception as e:
            # Clean up temp files on error
            for path in (temp_pkt_path, temp_rcpt_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            raise CheckpointStoreError(f"Failed to atomically save packet '{packet.task_id}': {e}") from e

        return target_path

    def load_packet(self, task_id: str) -> HandOffPacket:
        """Load HandOffPacket from store with corruption detection and quarantine policy."""
        with self._lock_store():
            return self._load_packet_unlocked(task_id)

    def _load_packet_unlocked(self, task_id: str) -> HandOffPacket:
        target_path = self._get_packet_path(task_id)
        if not os.path.exists(target_path):
            raise CheckpointStoreError(f"No checkpoint file found for task_id '{task_id}'")

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                record = json.load(f)

            if not isinstance(record, dict) or "packet" not in record or "checksum" not in record:
                raise CheckpointStoreError("Missing required envelope fields ('packet', 'checksum')")

            data = record["packet"]
            expected_checksum = record["checksum"]
            packet_json = json.dumps(data, indent=2, sort_keys=True)
            actual_checksum = hashlib.sha256(packet_json.encode("utf-8")).hexdigest()

            if expected_checksum != actual_checksum:
                raise CheckpointStoreError(
                    f"Checksum mismatch for task_id '{task_id}': expected {expected_checksum}, got {actual_checksum}"
                )

            return HandOffPacket.from_dict(data)

        except (json.JSONDecodeError, CheckpointStoreError, ValidationError) as e:
            corrupt_path = self._quarantine_corrupt_file(target_path, str(e))
            raise CheckpointStoreError(
                f"Checkpoint file for task '{task_id}' was corrupt or invalid ({e}). Quarantined to '{corrupt_path}'"
            ) from e

    def _quarantine_corrupt_file(self, path: str, reason: str) -> str:
        """Quarantine a corrupt checkpoint file by renaming it with timestamp."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(path)
        corrupt_filename = f"corrupt_{ts}_{filename}"
        corrupt_path = os.path.join(self.store_dir, corrupt_filename)
        try:
            if os.path.exists(path):
                shutil.move(path, corrupt_path)
                meta_path = f"{corrupt_path}.reason"
                with open(meta_path, "w", encoding="utf-8") as f:
                    f.write(f"Quarantined at: {datetime.now(timezone.utc).isoformat()}\nReason: {reason}\n")
        except OSError:
            pass
        return corrupt_path

    def is_receipt_seen(self, receipt_id: str) -> bool:
        """Check if receipt_id has been seen in store."""
        with self._lock_store():
            registry = self._load_receipts_unlocked()
            return receipt_id in registry

    def _load_receipts_unlocked(self) -> Dict[str, str]:
        if not os.path.exists(self.receipts_file):
            return {}
        try:
            with open(self.receipts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            corrupt_path = self._quarantine_corrupt_file(self.receipts_file, f"Corrupted registry: {e}")
            raise CheckpointStoreError(f"Receipt registry was corrupt. Quarantined to '{corrupt_path}'") from e
        return {}

    def verify_and_queue_packet(
        self,
        task_id: str,
        authorized_by: str,
        current_repo_sha: Optional[str] = None,
        workspace_root: Optional[str] = None,
        completed_dependency_task_ids: Optional[set[str]] = None,
        astra_verifier: Optional[Any] = None,
    ) -> HandOffPacket:
        """Explicit, durable authorized verification phase transitioning PROPOSED -> VERIFIED -> QUEUED."""
        with self._lock_store():
            packet = self._load_packet_unlocked(task_id)

            if packet.status != "PROPOSED":
                raise CheckpointStoreError(f"Cannot verify task in status '{packet.status}', must be 'PROPOSED'")

            packet.validate_security_and_constraints(
                current_repo_sha=current_repo_sha,
                workspace_root=workspace_root,
                completed_dependency_task_ids=completed_dependency_task_ids,
                astra_verifier=astra_verifier,
            )

            packet.transition_to("VERIFIED")
            packet.transition_to("QUEUED")
            packet.metadata["verified_by"] = authorized_by
            packet.metadata["verified_at_utc"] = datetime.now(timezone.utc).isoformat()

            self._save_packet_unlocked(packet)
            return packet

    def acquire_lease(
        self,
        task_id: str,
        owner: str,
        duration_seconds: int = 3600,
        current_repo_sha: Optional[str] = None,
        workspace_root: Optional[str] = None,
        completed_dependency_task_ids: Optional[set[str]] = None,
        now_utc: Optional[datetime] = None,
        astra_verifier: Optional[Any] = None,
    ) -> HandOffPacket:
        """Attempt to acquire lease on task packet fail-closed against active owner conflicts and unverified status."""
        with self._lock_store():
            packet = self._load_packet_unlocked(task_id)
            current_dt = now_utc or datetime.now(timezone.utc)

            if packet.status == "PROPOSED":
                raise CheckpointStoreError("Cannot acquire lease on packet in state 'PROPOSED'. Explicit authorized verification required.")

            if packet.status not in {"QUEUED", "PAUSED_QUOTA", "LEASED"}:
                raise CheckpointStoreError(f"Cannot acquire lease on packet in status '{packet.status}'")

            # Validate constraints fail-closed
            packet.validate_security_and_constraints(
                current_repo_sha=current_repo_sha,
                workspace_root=workspace_root,
                completed_dependency_task_ids=completed_dependency_task_ids,
                now_utc=current_dt,
                astra_verifier=astra_verifier,
            )

            # Check existing lease active state
            if packet.lease_owner and packet.lease_owner != owner:
                if packet.lease_expires_at_utc:
                    exp_dt = datetime.fromisoformat(packet.lease_expires_at_utc.replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if current_dt <= exp_dt:
                        raise CheckpointStoreError(
                            f"Active lease held by different owner '{packet.lease_owner}' until {packet.lease_expires_at_utc}"
                        )

            # Grant lease
            packet.lease_owner = owner
            packet.lease_epoch += 1
            exp_time = datetime.fromtimestamp(current_dt.timestamp() + duration_seconds, tz=timezone.utc)
            packet.lease_expires_at_utc = exp_time.isoformat()
            packet.idempotent_receipt_id = f"rcpt_{packet.task_id}_{packet.lease_epoch}"

            if packet.status in {"QUEUED", "PAUSED_QUOTA"}:
                packet.transition_to("LEASED")

            self._save_packet_unlocked(packet)
            return packet
