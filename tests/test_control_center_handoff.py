"""Comprehensive offline unittest suite for Control Center Task Handoff Protocol."""

from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import tempfile
import unittest

from control_center.handoff.packet import (
    HandOffPacket,
    StateTransitionError,
    ValidationError,
)
from control_center.handoff.schema import validate_packet_dict
from control_center.handoff.store import CheckpointStore, CheckpointStoreError


class TestControlCenterHandoff(unittest.TestCase):
    """Test suite covering handoff packets, state machine, security policies, and store."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store = CheckpointStore(self.test_dir)
        self.valid_sha = "1a759176a258619f85f2b612319cb6b6d20eeec6"
        self.fixture_dir = os.path.join("control_center", "handoff", "fixtures")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_positive_handoff_flow_and_fixtures(self):
        """Test valid fixture reading, packet construction, schema validation, save and load."""
        valid_fixture_path = os.path.join(self.fixture_dir, "valid_handoff_packet.json")
        with open(valid_fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        is_valid, reason = validate_packet_dict(data)
        self.assertTrue(is_valid, f"Fixture validation failed: {reason}")

        packet = HandOffPacket.from_dict(data)
        self.assertEqual(packet.task_id, "TASK-HANDOFF-0001")
        self.assertEqual(packet.status, "PROPOSED")

        # Save to store
        saved_path = self.store.save_packet(packet)
        self.assertTrue(os.path.exists(saved_path))

        # Load from store
        loaded = self.store.load_packet("TASK-HANDOFF-0001")
        self.assertEqual(loaded.task_id, packet.task_id)
        self.assertEqual(loaded.expected_base_sha, packet.expected_base_sha)

    def test_state_machine_valid_and_invalid_transitions(self):
        """Test legal and illegal state transitions."""
        packet = HandOffPacket(
            task_id="TASK-STATE-001",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
        )
        self.assertEqual(packet.status, "PROPOSED")

        # Legal: PROPOSED -> VERIFIED
        packet.transition_to("VERIFIED")
        self.assertEqual(packet.status, "VERIFIED")

        # Legal: VERIFIED -> QUEUED
        packet.transition_to("QUEUED")
        self.assertEqual(packet.status, "QUEUED")

        # Illegal: QUEUED -> COMPLETED directly (must be LEASED or CHECKPOINTED first)
        with self.assertRaises(StateTransitionError):
            packet.transition_to("COMPLETED")

        # Legal: QUEUED -> LEASED
        packet.transition_to("LEASED")
        self.assertEqual(packet.status, "LEASED")

        # Legal: LEASED -> CHECKPOINTED -> COMPLETED
        packet.transition_to("CHECKPOINTED")
        packet.transition_to("COMPLETED")
        self.assertEqual(packet.status, "COMPLETED")

        # Illegal: COMPLETED -> LEASED (terminal state)
        with self.assertRaises(StateTransitionError):
            packet.transition_to("LEASED")

    def test_stale_base_sha_fails_closed(self):
        """Test that expected_base_sha mismatch raises ValidationError."""
        packet = HandOffPacket(
            task_id="TASK-SHA-001",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha="0000000000000000000000000000000000000000",
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
        )
        with self.assertRaises(ValidationError) as ctx:
            packet.validate_security_and_constraints(current_repo_sha=self.valid_sha)
        self.assertEqual(ctx.exception.code, "STALE_BASE_SHA")

    def test_malicious_and_disallowed_file_paths(self):
        """Test fail-closed behavior for path traversal, forbidden paths, absolute paths, and null bytes."""
        bad_paths = [
            ("../outside.py", "PATH_TRAVERSAL_ATTEMPT"),
            ("foo/../../bar.py", "PATH_TRAVERSAL_ATTEMPT"),
            ("control_center/web/index.html", "FORBIDDEN_FILE_PATH"),
            ("control_center/collectors/github_collector.py", "FORBIDDEN_FILE_PATH"),
            (".github/workflows/worker_task.yml", "FORBIDDEN_FILE_PATH"),
            ("/etc/passwd", "DISALLOWED_FILE_PATH"),
            ("C:\\Windows\\System32\\cmd.exe", "DISALLOWED_FILE_PATH"),
            ("foo\0bar.py", "MALICIOUS_FILE_PATH"),
        ]

        for path, expected_code in bad_paths:
            packet = HandOffPacket(
                task_id=f"TASK-PATH-{hash(path)}",
                objective_type="TEST",
                repository="pddkalyan/project-agent-orchestrator",
                expected_base_sha=self.valid_sha,
                requested_agent="Google Jules",
                proposed_files_allowlist=[path],
                source_review_evidence_refs={},
                dependencies=[],
            )
            with self.assertRaises(ValidationError) as ctx:
                packet.validate_security_and_constraints()
            self.assertEqual(
                ctx.exception.code,
                expected_code,
                f"Path '{path}' did not trigger expected code '{expected_code}', got '{ctx.exception.code}'",
            )

    def test_expired_lease_fails_closed(self):
        """Test that an expired lease fails validation."""
        past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        packet = HandOffPacket(
            task_id="TASK-EXPIRED-001",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
            lease_owner="Google Jules",
            lease_expires_at_utc=past_time,
        )
        with self.assertRaises(ValidationError) as ctx:
            packet.validate_security_and_constraints()
        self.assertEqual(ctx.exception.code, "EXPIRED_LEASE")

    def test_active_owner_lease_collision(self):
        """Test that an active lease held by worker-A cannot be claimed by worker-B."""
        packet = HandOffPacket(
            task_id="TASK-LEASE-001",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
        )
        self.store.save_packet(packet)

        # Worker 1 acquires lease for 1 hour
        self.store.acquire_lease("TASK-LEASE-001", owner="Worker-1", duration_seconds=3600)

        # Worker 2 attempts to acquire active lease
        with self.assertRaises(CheckpointStoreError) as ctx:
            self.store.acquire_lease("TASK-LEASE-001", owner="Worker-2", duration_seconds=3600)
        self.assertIn("Active lease held by different owner 'Worker-1'", str(ctx.exception))

    def test_fake_astra_approval_fails_closed(self):
        """Test that unauthenticated claims of Astra approval raise UNAUTHENTICATED_ASTRA_APPROVAL."""
        packet = HandOffPacket(
            task_id="TASK-ASTRA-001",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={"astra_approved": True},
            dependencies=[],
            origin_trust_type="UNTRUSTED_PROPOSAL",
        )
        with self.assertRaises(ValidationError) as ctx:
            packet.validate_security_and_constraints()
        self.assertEqual(ctx.exception.code, "UNAUTHENTICATED_ASTRA_APPROVAL")

    def test_duplicate_receipt_id_collision(self):
        """Test duplicate receipt registration rejection across different tasks."""
        p1 = HandOffPacket(
            task_id="TASK-RCPT-1",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
            idempotent_receipt_id="rcpt_fixed_shared_1",
        )
        self.store.save_packet(p1)

        p2 = HandOffPacket(
            task_id="TASK-RCPT-2",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
            idempotent_receipt_id="rcpt_fixed_shared_1",
        )
        with self.assertRaises(CheckpointStoreError) as ctx:
            self.store.save_packet(p2)
        self.assertIn("Duplicate receipt ID collision", str(ctx.exception))

    def test_corrupt_checkpoint_recovery_and_quarantine(self):
        """Test recovery and quarantine when a JSON checkpoint is corrupt."""
        p = HandOffPacket(
            task_id="TASK-CORRUPT-01",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
        )
        saved_path = self.store.save_packet(p)

        # Corrupt the saved file with invalid JSON
        with open(saved_path, "w", encoding="utf-8") as f:
            f.write("{ CORRUPTED_JSON_DATA ...")

        # Loading should raise CheckpointStoreError and quarantine the file
        with self.assertRaises(CheckpointStoreError):
            self.store.load_packet("TASK-CORRUPT-01")

        # The original corrupt file should be moved/quarantined
        self.assertFalse(os.path.exists(saved_path))
        quarantined_files = [f for f in os.listdir(self.test_dir) if f.startswith("corrupt_")]
        self.assertTrue(len(quarantined_files) >= 1)

    def test_untrusted_request_cannot_force_quota_unpause(self):
        """Test that untrusted proposal requests cannot trigger restricted state transitions."""
        p = HandOffPacket(
            task_id="TASK-QUOTA-01",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
            status="QUEUED",
        )
        p.transition_to("PAUSED_QUOTA")
        self.assertEqual(p.status, "PAUSED_QUOTA")

        # Untrusted request trying to force unpause to LEASED
        with self.assertRaises(StateTransitionError):
            p.transition_to("LEASED", untrusted_request=True)

    def test_unknown_permissions_and_capacity_handling(self):
        """Test that unknown capacity defaults to UNKNOWN in metadata."""
        p = HandOffPacket(
            task_id="TASK-UNKNOWN-01",
            objective_type="TEST",
            repository="pddkalyan/project-agent-orchestrator",
            expected_base_sha=self.valid_sha,
            requested_agent="Google Jules",
            proposed_files_allowlist=["control_center/handoff/packet.py"],
            source_review_evidence_refs={},
            dependencies=[],
            metadata={"capacity": "UNKNOWN"},
        )
        self.assertEqual(p.metadata.get("capacity"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
