#!/usr/bin/env python3
"""
Adversarial offline unit tests for TASK-0004 Sign in with ChatGPT live adapter & CLI.

Worker: worker_gemini_cli / model: gemini-3.5-flash-lite
"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004 import (
    HostCredentialStorage,
    build_authorization_url,
    build_responses_plan_request,
    build_token_exchange_request,
    build_token_refresh_request,
    evaluate_live_handoff,
    generate_host_id,
    generate_pkce,
    map_network_or_http_error,
    parse_loopback_callback,
    sanitize_object,
    validate_model_catalog,
    verify_granted_scopes,
    verify_id_token,
)


class TestChatGPTPlanAuthAdapter(unittest.TestCase):
    def setUp(self):
        self.client_id = "test_client_id_123"
        self.redirect_uri = "http://127.0.0.1:8080/callback"

    def test_01_host_id_generation_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage_path = Path(tmp) / "session.json"
            storage = HostCredentialStorage(storage_path)
            self.assertIsNone(storage.load_profile())

            host_id_1 = generate_host_id()
            profile = {"ext_agent_host_id": host_id_1}
            self.assertTrue(storage.save_profile_atomic(profile))

            loaded = storage.load_profile()
            self.assertEqual(loaded["ext_agent_host_id"], host_id_1)

            # Reuse stable host ID
            loaded["notes"] = "updated"
            self.assertTrue(storage.save_profile_atomic(loaded))
            reloaded = storage.load_profile()
            self.assertEqual(reloaded["ext_agent_host_id"], host_id_1)
            self.assertEqual(reloaded["notes"], "updated")

    def test_02_pkce_and_auth_url_construction(self):
        auth_data = build_authorization_url(
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
        )
        self.assertIn("authorization_url", auth_data)
        self.assertIn("code_verifier", auth_data)
        self.assertIn("state", auth_data)
        self.assertIn("nonce", auth_data)
        self.assertIn("code_challenge_method=S256", auth_data["authorization_url"])
        self.assertIn("resource=https%3A%2F%2Fapi.openai.com%2Fv1", auth_data["authorization_url"])

    def test_03_loopback_callback_parsing_and_state_validation(self):
        auth_data = build_authorization_url(
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
        )
        expected_state = auth_data["state"]
        valid_callback = f"{self.redirect_uri}?code=auth_code_xyz&state={expected_state}"

        parsed = parse_loopback_callback(valid_callback, expected_state)
        self.assertEqual(parsed["code"], "auth_code_xyz")

        # State mismatch
        bad_callback = f"{self.redirect_uri}?code=auth_code_xyz&state=wrong_state"
        with self.assertRaises(ValueError):
            parse_loopback_callback(bad_callback, expected_state)

        # OAuth error
        error_callback = f"{self.redirect_uri}?error=access_denied&state={expected_state}"
        with self.assertRaises(ValueError):
            parse_loopback_callback(error_callback, expected_state)

    def test_04_token_exchange_and_refresh_request_builders(self):
        exchange = build_token_exchange_request(
            client_id=self.client_id,
            code="code_123",
            code_verifier="verifier_456",
            redirect_uri=self.redirect_uri,
        )
        self.assertEqual(exchange["method"], "POST")
        self.assertEqual(exchange["data"]["grant_type"], "authorization_code")
        self.assertEqual(exchange["data"]["code_verifier"], "verifier_456")

        refresh = build_token_refresh_request(
            client_id=self.client_id,
            refresh_token="ref_789",
        )
        self.assertEqual(refresh["method"], "POST")
        self.assertEqual(refresh["data"]["grant_type"], "refresh_token")
        self.assertEqual(refresh["data"]["refresh_token"], "ref_789")

    def test_05_scope_enforcement(self):
        ok_scopes = ["openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"]
        valid, _ = verify_granted_scopes(ok_scopes)
        self.assertTrue(valid)

        # Missing chatgpt.tokens.use.direct
        bad_scopes_1 = ["openid", "profile", "email", "offline_access", "resource.invoke"]
        valid_1, reason_1 = verify_granted_scopes(bad_scopes_1)
        self.assertFalse(valid_1)
        self.assertIn("chatgpt.tokens.use.direct", reason_1)

        # Missing offline_access
        bad_scopes_2 = ["openid", "profile", "email", "resource.invoke", "chatgpt.tokens.use.direct"]
        valid_2, reason_2 = verify_granted_scopes(bad_scopes_2)
        self.assertFalse(valid_2)
        self.assertIn("offline_access", reason_2)

    def test_06_id_token_validation(self):
        import time
        claims = {
            "iss": "https://auth.openai.com",
            "aud": self.client_id,
            "nonce": "nonce_abc",
            "exp": time.time() + 3600,
        }
        claims_bytes = json.dumps(claims).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(claims_bytes).rstrip(b"=").decode("ascii")
        fake_id_token = f"header.{payload_b64}.signature"

        valid, reason = verify_id_token(
            fake_id_token,
            expected_client_id=self.client_id,
            expected_nonce="nonce_abc",
        )
        self.assertTrue(valid, reason)

        # Nonce mismatch
        valid_nonce, _ = verify_id_token(
            fake_id_token,
            expected_client_id=self.client_id,
            expected_nonce="wrong_nonce",
        )
        self.assertFalse(valid_nonce)

        # Expired
        expired_claims = dict(claims, exp=time.time() - 10)
        expired_bytes = json.dumps(expired_claims).encode("utf-8")
        expired_b64 = base64.urlsafe_b64encode(expired_bytes).rstrip(b"=").decode("ascii")
        expired_token = f"header.{expired_b64}.signature"
        valid_exp, _ = verify_id_token(
            expired_token,
            expected_client_id=self.client_id,
            expected_nonce="nonce_abc",
        )
        self.assertFalse(valid_exp)

    def test_07_exact_gpt_6_astra_model_enforcement(self):
        valid_catalog = {"user_authorized": True, "authorized_models": ["gpt-6-astra", "gpt-4o"]}
        self.assertEqual(validate_model_catalog(valid_catalog), (True, "MODEL_ALLOWED"))

        # Similar names rejected
        invalid_catalogs = [
            {"user_authorized": True, "authorized_models": ["gpt-6-astra-preview"]},
            {"user_authorized": True, "authorized_models": ["gpt-6"]},
            {"user_authorized": False, "authorized_models": ["gpt-6-astra"]},
            None,
        ]
        for cat in invalid_catalogs:
            valid, status = validate_model_catalog(cat)
            self.assertFalse(valid)
            self.assertEqual(status, "BLOCKED_NO_ASTRA")

    def test_08_stateless_responses_api_request_builder(self):
        req = build_responses_plan_request(
            prompt_instructions="Review this plan.",
            review_context={"task_id": "TASK-0004"},
        )
        self.assertEqual(req["model"], "gpt-6-astra")
        self.assertEqual(req["store"], False)
        self.assertEqual(req["stream"], True)
        self.assertEqual(req["input"], "Review this plan.")
        self.assertIn("context", req)

        # Missing instructions or context raises ValueError
        with self.assertRaises(ValueError):
            build_responses_plan_request(prompt_instructions="", review_context={})

    def test_09_error_status_mapping(self):
        self.assertEqual(map_network_or_http_error(401, "Unauthorized"), "BLOCKED_AUTH_REQUIRED")
        self.assertEqual(map_network_or_http_error(403, "Forbidden"), "BLOCKED_AUTH_REQUIRED")
        self.assertEqual(map_network_or_http_error(429, "Too Many Requests"), "BLOCKED_PLAN_ALLOWANCE")
        self.assertEqual(map_network_or_http_error(500, "Internal Server Error"), "BLOCKED_INFRASTRUCTURE_ERROR")
        self.assertEqual(map_network_or_http_error(None, "Connection timeout"), "BLOCKED_NETWORK_ERROR")

    def test_10_credential_sanitization_in_objects(self):
        sensitive_data = {
            "profile_id": "safe_profile_1",
            "access_token": "secret_token_val_12345",
            "refresh_token": "secret_refresh_val_67890",
            "nested": {
                "client_secret": "my_secret_key_abc",
            },
        }
        cleaned = sanitize_object(sensitive_data)
        self.assertEqual(cleaned["profile_id"], "safe_profile_1")
        # Check that secret fields are redacted
        self.assertTrue(any("redacted" in str(v) or v == "[REDACTED-POTENTIAL-SECRET]" for v in cleaned.values()))
