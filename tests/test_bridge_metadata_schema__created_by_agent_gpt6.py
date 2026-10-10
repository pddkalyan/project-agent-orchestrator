"""Offline tests for redacted native metadata probes."""
import json
import unittest

from control_center.remote_bridge.check_agentapi_metadata__created_by_agent_gpt6 import (
    summarize_metadata,
)


class TestReadOnlyMetadataSchema(unittest.TestCase):
    def test_correct_schema_reports_match_without_exposing_id(self):
        expected = "private-conversation-value"
        report = summarize_metadata(json.dumps({
            "conversation_id": expected,
            "workspace": "C:\\\\User\\\\private-project",
            "model": "private-model",
        }), expected)
        self.assertEqual(report["verdict"], "SCHEMA_VERIFIED")
        self.assertTrue(report["exact_conversation_id_match"])
        serialized = json.dumps(report)
        self.assertNotIn(expected, serialized)
        self.assertNotIn("private-project", serialized)
        self.assertIn("conversation_id", report["field_names"])

    def test_missing_id_or_wrong_schema_fails_closed(self):
        expected = "private-value"
        for raw in ("{}", '[]', "garbled", '{"conversationId":"private-value"}',
                    '{"conversation_id":""}', '{"conversation_id":"different"}'):
            with self.subTest(raw=raw):
                report = summarize_metadata(raw, expected)
                self.assertEqual(report["verdict"], "FAIL_CLOSED")
                self.assertFalse(report["exact_conversation_id_match"])

    def test_unexpected_unicode_and_long_values_do_not_leak(self):
        expected = "private-value"
        report = summarize_metadata(json.dumps({
            "conversation_id": "someone_else_" + "\\u200b" * 500,
            "metadata": {"access_token": "secret"},
        }), expected)
        self.assertEqual(report["verdict"], "FAIL_CLOSED")
        self.assertNotIn("secret", json.dumps(report))
        self.assertFalse(report["exact_conversation_id_match"])


if __name__ == "__main__":
    unittest.main()
