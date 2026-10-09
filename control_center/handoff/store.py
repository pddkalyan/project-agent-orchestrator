"""Atomic local JSON checkpoint store with corruption recovery and lock management."""

from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

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

    def _get_packet_path(self, task_id: str) -> str:
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        return os.path.join(self.store_dir, f"packet_{safe_id}.json")

    def save_packet(self, packet: HandOffPacket) -> str:
        """Atomic save of a HandOffPacket to local JSON file with SHA-256 checksum."""
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
        temp_fd, temp_path = tempfile.mkstemp(dir=self.store_dir, prefix="tmp_pkt_")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                f.write(record_json)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target_path)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise CheckpointStoreError(f"Failed to atomically save packet '{packet.task_id}': {e}") from e

        # Register receipt ID to prevent replay/duplicate receipts
        self._register_receipt(packet.idempotent_receipt_id, packet.task_id)
        return target_path

    def load_packet(self, task_id: str) -> HandOffPacket:
        """Load HandOffPacket from store with corruption detection and quarantine policy."""
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
            # Corruption recovery policy: quarantine corrupt file
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

    def _register_receipt(self, receipt_id: str, task_id: str) -> None:
        """Maintain idempotent receipt registry in store."""
        registry = self._load_receipts()
        if receipt_id in registry and registry[receipt_id] != task_id:
            raise CheckpointStoreError(
                f"Duplicate receipt ID collision: '{receipt_id}' already registered for task '{registry[receipt_id]}'"
            )
        registry[receipt_id] = task_id
        self._save_receipts(registry)

    def is_receipt_seen(self, receipt_id: str) -> bool:
        """Check if receipt_id has been seen in store."""
        registry = self._load_receipts()
        return receipt_id in registry

    def _load_receipts(self) -> Dict[str, str]:
        if not os.path.exists(self.receipts_file):
            return {}
        try:
            with open(self.receipts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_receipts(self, registry: Dict[str, str]) -> None:
        temp_fd, temp_path = tempfile.mkstemp(dir=self.store_dir, prefix="tmp_rcpt_")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.receipts_file)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise CheckpointStoreError(f"Failed to update receipt registry: {e}") from e

    def acquire_lease(
        self,
        task_id: str,
        owner: str,
        duration_seconds: int = 3600,
        now_utc: Optional[datetime] = None,
    ) -> HandOffPacket:
        """Attempt to acquire lease on task packet fail-closed against active owner conflicts."""
        packet = self.load_packet(task_id)
        current_dt = now_utc or datetime.now(timezone.utc)

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

        if packet.status == "PROPOSED":
            packet.transition_to("VERIFIED")
        if packet.status == "VERIFIED":
            packet.transition_to("QUEUED")
        if packet.status in {"QUEUED", "PAUSED_QUOTA"}:
            packet.transition_to("LEASED")

        self.save_packet(packet)
        return packet
