#!/usr/bin/env python3
"""Offline deterministic tests for TASK-0004 Sign in with ChatGPT adapter."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import time
import unittest
import urllib.parse
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "scripts" / "chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py"
spec = importlib.util.spec_from_file_location("task0004_adapter", ADAPTER_PATH)
adapter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(adapter)


class FakeProtector(adapter.SecretProtector):
    def protect(self, plaintext: bytes) -> bytes:
        return b"ENC:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"ENC:"):
            raise ValueError("bad ciphertext")
        return ciphertext[4:][::-1]


class FakeTransport:
    def __init__(self):
        self.post_calls = []
        self.get_calls = []
        self.refresh_payload = None
        self.models_payload = None

    def post_form(self, url, data, headers=None):
        self.post_calls.append((url, deepcopy(data), deepcopy(headers)))
        if self.refresh_payload is None:
            raise RuntimeError("no fake response")
        return deepcopy(self.refresh_payload)

    def get_json(self, url, headers=None):
        self.get_calls.append((url, deepcopy(headers)))
        if self.models_payload is None:
            raise RuntimeError("no fake response")
        return deepcopy(self.models_payload)


class TestTask0004(unittest.TestCase):
    def setUp(self):
        self.host_id = "urn:uuid:12345678-1234-4abc-8def-1234567890ab"
        self.redirect = "http://127.0.0.1:1455/auth/callback"
        self.attempt = {
            "state": "state123",
            "nonce": "nonce123",
            "code_verifier": "verifier123",
            "code_challenge": "challenge123",
        }

    def test_01_host_id_format(self):
        host = adapter.generate_host_id()
        self.assertTrue(adapter.validate_host_id(host))
        self.assertTrue(host.startswith("urn:uuid:"))

    def test_02_invalid_host_id_rejected(self):
        for value in ["host_win_abc", "urn:uuid:not-a-uuid", "", None]:
            self.assertFalse(adapter.validate_host_id(value))

    def test_03_pkce_s256(self):
        verifier, challenge = adapter.generate_pkce()
        import base64, hashlib
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)

    def test_04_new_attempt_has_fresh_fields(self):
        a = adapter.new_authorization_attempt()
        b = adapter.new_authorization_attempt()
        for key in ("state", "nonce", "code_verifier", "code_challenge"):
            self.assertTrue(a[key])
            self.assertNotEqual(a[key], b[key])

    def test_05_loopback_requires_127(self):
        self.assertTrue(adapter.validate_loopback_redirect_uri(self.redirect))
        self.assertFalse(adapter.validate_loopback_redirect_uri("http://localhost:1455/auth/callback"))
        self.assertFalse(adapter.validate_loopback_redirect_uri("http://127.0.0.1:1455/callback"))
        self.assertFalse(adapter.validate_loopback_redirect_uri("https://127.0.0.1:1455/auth/callback"))

    def test_06_first_registration_url_is_current_openai_flow(self):
        url = adapter.build_authorization_url(
            redirect_uri=self.redirect,
            ext_agent_host_id=self.host_id,
            attempt=self.attempt,
        )
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.geturl().split("?")[0], "https://auth.openai.com/api/accounts/authorize")
        self.assertEqual(qs["client_id"], ["dynamic_agent_client"])
        self.assertEqual(qs["agent_name_hint"], [adapter.AGENT_NAME])
        self.assertEqual(qs["ext_agent_host_id"], [self.host_id])
        self.assertEqual(qs["resource"], ["https://api.openai.com/v1"])
        self.assertEqual(qs["code_challenge_method"], ["S256"])
        self.assertIn("chatgpt.tokens.use.direct", qs["scope"][0])
        self.assertIn("offline_access", qs["scope"][0])

    def test_07_returning_url_reuses_issued_client_without_agent_name(self):
        url = adapter.build_authorization_url(
            redirect_uri=self.redirect,
            ext_agent_host_id=self.host_id,
            attempt=self.attempt,
            issued_client_id="oaiapp_issued",
            retained_id_token="idtokenhint",
            login_hint="user@example.com",
        )
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(qs["client_id"], ["oaiapp_issued"])
        self.assertNotIn("agent_name_hint", qs)
        self.assertEqual(qs["id_token_hint"], ["idtokenhint"])
        self.assertEqual(qs["login_hint"], ["user@example.com"])

    def test_08_callback_requires_state_before_code(self):
        with self.assertRaises(ValueError):
            adapter.parse_loopback_callback(
                "?code=abc&state=wrong&client_id=oaiapp_x",
                expected_state="expected",
                expected_client_id=None,
                is_new_registration=True,
            )

    def test_09_new_callback_requires_issued_client(self):
        with self.assertRaises(ValueError):
            adapter.parse_loopback_callback(
                "?code=abc&state=s",
                expected_state="s",
                expected_client_id=None,
                is_new_registration=True,
            )

    def test_10_new_callback_returns_issued_client(self):
        result = adapter.parse_loopback_callback(
            "?code=abc&state=s&client_id=oaiapp_x&scope=offline_access+chatgpt.tokens.use.direct",
            expected_state="s",
            expected_client_id=None,
            is_new_registration=True,
        )
        self.assertEqual(result["client_id"], "oaiapp_x")

    def test_11_returning_callback_rejects_different_client(self):
        with self.assertRaises(ValueError):
            adapter.parse_loopback_callback(
                "?code=abc&state=s&client_id=oaiapp_other",
                expected_state="s",
                expected_client_id="oaiapp_saved",
                is_new_registration=False,
            )

    def test_12_token_endpoint_and_exchange_require_issued_client(self):
        req = adapter.build_token_exchange_request(
            client_id="oaiapp_x",
            code="code",
            code_verifier="verifier",
            redirect_uri=self.redirect,
        )
        self.assertEqual(req["url"], "https://auth.openai.com/api/accounts/oauth/token")
        self.assertNotIn("client_secret", req["data"])
        with self.assertRaises(ValueError):
            adapter.build_token_exchange_request(
                client_id="dynamic_agent_client",
                code="code",
                code_verifier="verifier",
                redirect_uri=self.redirect,
            )

    def test_13_refresh_request_omits_scope(self):
        req = adapter.build_token_refresh_request(client_id="oaiapp_x", refresh_token="refresh")
        self.assertEqual(req["url"], "https://auth.openai.com/api/accounts/oauth/token")
        self.assertNotIn("scope", req["data"])
        self.assertEqual(req["data"]["resource"], "https://api.openai.com/v1")

    def test_14_scope_enforcement(self):
        self.assertTrue(adapter.verify_granted_scopes("openid offline_access chatgpt.tokens.use.direct")[0])
        self.assertFalse(adapter.verify_granted_scopes("openid offline_access")[0])
        self.assertFalse(adapter.verify_granted_scopes("openid chatgpt.tokens.use.direct")[0])

    def test_15_signature_verifier_is_mandatory(self):
        ok, reason, claims = adapter.verify_id_token(
            "x.y.z",
            expected_client_id="oaiapp_x",
            expected_nonce="n",
            signature_verifier=None,
        )
        self.assertFalse(ok)
        self.assertIn("required", reason)
        self.assertIsNone(claims)

    def test_16_verified_claims_require_exact_issuer_aud_nonce_sub_exp(self):
        now = time.time()
        base = {"iss": adapter.ISSUER, "aud": "oaiapp_x", "nonce": "n", "sub": "s", "exp": now + 60}
        self.assertTrue(adapter.validate_verified_id_claims(base, expected_client_id="oaiapp_x", expected_nonce="n", now=now)[0])
        for field, bad in [("iss", adapter.ISSUER + "/x"), ("aud", "other"), ("nonce", "other"), ("sub", ""), ("exp", now - 1)]:
            claims = dict(base); claims[field] = bad
            self.assertFalse(adapter.validate_verified_id_claims(claims, expected_client_id="oaiapp_x", expected_nonce="n", now=now)[0])

    def test_17_fake_signature_verifier_failure_blocks(self):
        def bad(_token):
            raise ValueError("signature bad")
        self.assertFalse(adapter.verify_id_token("x.y.z", expected_client_id="oaiapp_x", expected_nonce="n", signature_verifier=bad)[0])

    def test_18_fake_signature_verifier_success(self):
        claims = {"iss": adapter.ISSUER, "aud": "oaiapp_x", "nonce": "n", "sub": "s", "exp": time.time() + 60}
        ok, _, returned = adapter.verify_id_token("x.y.z", expected_client_id="oaiapp_x", expected_nonce="n", signature_verifier=lambda _: claims)
        self.assertTrue(ok)
        self.assertEqual(returned["sub"], "s")

    def test_19_storage_never_writes_plaintext_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            storage = adapter.HostCredentialStorage(path, FakeProtector())
            profile = {
                "client_id": "oaiapp_x",
                "access_token": "ACCESS_SECRET_SENTINEL",
                "refresh_token": "REFRESH_SECRET_SENTINEL",
                "id_token": "ID_SECRET_SENTINEL",
            }
            storage.save_profile_atomic(profile)
            raw = path.read_text()
            self.assertNotIn("ACCESS_SECRET_SENTINEL", raw)
            self.assertNotIn("REFRESH_SECRET_SENTINEL", raw)
            self.assertNotIn("ID_SECRET_SENTINEL", raw)
            self.assertEqual(storage.load_profile()["access_token"], "ACCESS_SECRET_SENTINEL")

    def test_20_safe_status_is_credential_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({
                "profile_label": "p",
                "client_id": "oaiapp_x",
                "ext_agent_host_id": self.host_id,
                "access_token": "ACCESS_SECRET_SENTINEL",
                "refresh_token": "REFRESH_SECRET_SENTINEL",
                "id_token": "ID_SECRET_SENTINEL",
                "scopes": ["offline_access", "chatgpt.tokens.use.direct"],
            })
            encoded = json.dumps(storage.safe_status())
            self.assertNotIn("ACCESS_SECRET_SENTINEL", encoded)
            self.assertNotIn("REFRESH_SECRET_SENTINEL", encoded)
            self.assertNotIn("ID_SECRET_SENTINEL", encoded)

    def test_21_refresh_rotation_replaces_token_set_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({
                "profile_label": "p",
                "client_id": "oaiapp_x",
                "refresh_token": "old_refresh",
                "access_token": "old_access",
                "id_token": "old_id",
                "scopes": ["offline_access", "chatgpt.tokens.use.direct"],
            })
            transport = FakeTransport()
            transport.refresh_payload = {
                "access_token": "new_access",
                "refresh_token": "new_refresh",
                "id_token": "new_id",
                "expires_in": 3600,
                "scope": "offline_access chatgpt.tokens.use.direct",
            }
            result = adapter.refresh_profile(storage, transport)
            self.assertEqual(result["status"], "REFRESHED")
            profile = storage.load_profile()
            self.assertEqual(profile["refresh_token"], "new_refresh")
            self.assertEqual(profile["access_token"], "new_access")

    def test_22_refresh_failure_does_not_replace_old_profile(self):
        class FailingStorage(adapter.HostCredentialStorage):
            def save_profile_atomic(self, profile):
                raise OSError("persistence failure")
        with tempfile.TemporaryDirectory() as tmp:
            base = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            base.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"old","access_token":"old_access","id_token":"old_id","scopes":["offline_access","chatgpt.tokens.use.direct"]})
            storage = FailingStorage(base.storage_path, base.protector)
            transport = FakeTransport()
            transport.refresh_payload = {"access_token":"new","refresh_token":"newr","id_token":"newid","expires_in":3600,"scope":"offline_access chatgpt.tokens.use.direct"}
            with self.assertRaises(OSError):
                adapter.refresh_profile(storage, transport)
            self.assertEqual(base.load_profile()["refresh_token"], "old")

    def test_23_refresh_lock_serializes_two_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","scopes":["offline_access","chatgpt.tokens.use.direct"]})
            class Rotating(FakeTransport):
                def post_form(self, url, data, headers=None):
                    time.sleep(0.03)
                    current = data["refresh_token"]
                    n = int(current[1:]) + 1
                    return {"access_token":f"a{n}","refresh_token":f"r{n}","id_token":f"i{n}","expires_in":3600,"scope":"offline_access chatgpt.tokens.use.direct"}
            t = Rotating()
            results = []
            threads = [threading.Thread(target=lambda: results.append(adapter.refresh_profile(storage,t))) for _ in range(2)]
            for th in threads: th.start()
            for th in threads: th.join()
            self.assertEqual(storage.load_profile()["refresh_token"], "r2")
            self.assertEqual(len(results), 2)

    def test_24_model_catalog_parses_visible_slugs_only(self):
        catalog = adapter.parse_model_catalog({"models":[
            {"slug":"gpt-6-astra","visibility":"list"},
            {"slug":"hidden","visibility":"hidden"},
        ]})
        self.assertEqual(catalog["authorized_models"], ["gpt-6-astra"])

    def test_25_exact_astra_only(self):
        self.assertTrue(adapter.validate_model_catalog({"user_authorized":True,"authorized_models":["gpt-6-astra"]})[0])
        for name in ["gpt-6-astra-preview","GPT-6-ASTRA","gpt-6.1-sol"]:
            self.assertFalse(adapter.validate_model_catalog({"user_authorized":True,"authorized_models":[name]})[0])

    def test_26_responses_request_has_stateless_supported_shape(self):
        req = adapter.build_responses_plan_request(review_prompt="Review exact SHA.", review_context={"task_id":"TASK-0004","candidate_sha":"a"*40})
        self.assertEqual(set(req), {"model","input","instructions","store","stream"})
        self.assertFalse(req["store"])
        self.assertTrue(req["stream"])
        self.assertNotIn("context", req)

    def test_27_responses_builder_rejects_unsupported_stateful_fields(self):
        for field in adapter.UNSUPPORTED_RESPONSE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    adapter.build_responses_plan_request(review_prompt="x", review_context={field:"bad"})

    def test_28_stream_requires_completed_event(self):
        incomplete = [{"type":"response.output_text.delta","delta":"hello"}]
        self.assertEqual(adapter.assemble_stream(incomplete)["status"], adapter.BLOCKED_INVALID_RESPONSE)
        complete = incomplete + [{"type":"response.completed"}]
        result = adapter.assemble_stream(complete)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["text"], "hello")

    def test_29_stream_usage_limit_maps_to_plan_block(self):
        events=[{"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}]
        self.assertEqual(adapter.assemble_stream(events)["status"], adapter.BLOCKED_PLAN_ALLOWANCE)

    def test_30_error_mapping_never_becomes_review_verdict(self):
        for code in [401,403,429,500,503]:
            status=adapter.map_http_status(code)
            self.assertTrue(status.startswith("BLOCKED_"))
            self.assertNotIn(status, ("APPROVED","REJECTED"))

    def test_31_handoff_strips_live_credentials_before_task0003(self):
        class FakeBridge:
            def evaluate_review(self, **kwargs):
                return kwargs["auth_profile"]
        old=adapter.bridge
        adapter.bridge=FakeBridge()
        try:
            result=adapter.evaluate_live_handoff(
                expected_snapshot={},
                review_request={},
                model_catalog={},
                auth_profile={"active":True,"expired":False,"profile_id":"p","access_token":"SECRET"},
                plan_allowance={},
                verdict_payload={},
            )
            self.assertEqual(result, {"active":True,"expired":False,"profile_id":"p"})
        finally:
            adapter.bridge=old

    def test_32_sanitizer_removes_tokens_and_id_token_hint(self):
        value={"access_token":"ACCESS_SENTINEL","url":"https://auth.openai.com/x?id_token_hint=ID_SENTINEL"}
        encoded=json.dumps(adapter.sanitize_object(value))
        self.assertNotIn("ACCESS_SENTINEL", encoded)
        self.assertNotIn("ID_SENTINEL", encoded)

    def test_33_self_check_preserves_zero_spend_no_conversation_no_merge(self):
        check=adapter.self_check()
        self.assertTrue(check["zero_extra_spend"])
        self.assertFalse(check["api_key_path"])
        self.assertFalse(check["paid_fallback"])
        self.assertFalse(check["ci_live_authorization"])
        self.assertFalse(check["conversation_access"])
        self.assertFalse(check["local_inference"])
        self.assertFalse(check["merge_allowed"])

    def test_34_source_has_no_openai_api_key_or_merge_path(self):
        text=ADAPTER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("OPENAI_API_KEY", text)
        self.assertNotIn("merge_pull_request", text)
        self.assertNotIn("enable_auto_merge", text)
        self.assertNotIn("/conversations", text)

    def test_35_live_jwks_verifier_endpoint_is_official(self):
        self.assertEqual(adapter.JWKS_URL, "https://auth.openai.com/.well-known/jwks.json")

    def test_36_public_client_has_no_client_secret_path(self):
        text=ADAPTER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("OPENAI_CLIENT_SECRET", text)
        self.assertNotIn('"client_secret"', text)


    def test_37_refresh_may_retain_existing_id_token_and_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({
                "client_id":"oaiapp_x",
                "refresh_token":"r0",
                "access_token":"a0",
                "id_token":"retained_id",
                "scopes":["offline_access","chatgpt.tokens.use.direct"],
            })
            transport = FakeTransport()
            transport.refresh_payload = {
                "access_token":"a1",
                "refresh_token":"r1",
                "expires_in":3600,
            }
            self.assertEqual(adapter.refresh_profile(storage, transport)["status"], "REFRESHED")
            profile = storage.load_profile()
            self.assertEqual(profile["id_token"], "retained_id")
            self.assertIn("chatgpt.tokens.use.direct", profile["scopes"])

    def test_38_needs_refresh_obeys_expiry_and_earliest_refresh(self):
        now = 1000
        self.assertTrue(adapter.needs_refresh({"expires_at": 1050}, now=now))
        self.assertFalse(adapter.needs_refresh({"expires_at": 5000}, now=now))
        self.assertFalse(adapter.needs_refresh({"expires_at": 1050, "earliest_refresh_at": 2000}, now=now))

    def test_39_ensure_fresh_profile_uses_refresh_when_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({
                "client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0",
                "scopes":["offline_access","chatgpt.tokens.use.direct"],"expires_at":0
            })
            transport = FakeTransport()
            transport.refresh_payload = {"access_token":"a1","refresh_token":"r1","expires_in":3600}
            profile, status = adapter.ensure_fresh_profile(storage, transport)
            self.assertEqual(status, "PROFILE_READY")
            self.assertEqual(profile["access_token"], "a1")

    def test_40_run_streamed_review_uses_bearer_and_completed_stream(self):
        class StreamTransport:
            def __init__(self):
                self.headers = None
                self.payload = None
            def stream_sse(self, url, payload, headers):
                self.headers = headers
                self.payload = payload
                yield {"type":"response.output_text.delta","delta":"{\"status\":\"APPROVED\"}"}
                yield {"type":"response.completed"}
        t=StreamTransport()
        result=adapter.run_streamed_review(
            profile={"access_token":"ACCESS_SENTINEL"},
            transport=t,
            review_prompt="Review.",
            review_context={"task_id":"TASK-0004"},
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(t.payload["store"], False)
        self.assertEqual(t.payload["stream"], True)
        self.assertEqual(t.headers["Authorization"], "Bearer ACCESS_SENTINEL")

    def test_41_run_streamed_review_transport_failure_is_blocked_not_verdict(self):
        class BadTransport:
            def stream_sse(self, *args, **kwargs):
                raise RuntimeError("network timeout")
        result=adapter.run_streamed_review(
            profile={"access_token":"x"},
            transport=BadTransport(),
            review_prompt="Review.",
            review_context={"task_id":"TASK-0004"},
        )
        self.assertEqual(result["status"], adapter.BLOCKED_NETWORK_ERROR)
        self.assertNotIn(result["status"], ("APPROVED","REJECTED"))


    def test_42_refresh_error_codes_fail_closed_as_auth(self):
        for code in [
            "invalid_grant",
            "invalid_refresh_token",
            "refresh_token_expired",
            "refresh_token_invalidated",
            "refresh_token_reused",
            "invalid_client",
        ]:
            with self.subTest(code=code):
                self.assertEqual(adapter.map_transport_error(code), adapter.BLOCKED_AUTH_REQUIRED)

    def test_43_subscription_route_capability_errors_are_not_auth_or_verdicts(self):
        for code in [
            "subscription_sharing_unsupported_capability",
            "subscription_sharing_route_not_supported",
        ]:
            status = adapter.map_transport_error(code)
            self.assertEqual(status, adapter.BLOCKED_INVALID_RESPONSE)
            self.assertNotIn(status, ("APPROVED", "REJECTED"))

    def test_44_profile_files_keep_account_registrations_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = adapter.HostCredentialStorage(Path(tmp) / "first.json", FakeProtector())
            second = adapter.HostCredentialStorage(Path(tmp) / "second.json", FakeProtector())
            first.save_profile_atomic({
                "profile_label":"first","client_id":"oaiapp_first","subject":"sub1",
                "access_token":"a1","refresh_token":"r1","id_token":"i1"
            })
            second.save_profile_atomic({
                "profile_label":"second","client_id":"oaiapp_second","subject":"sub2",
                "access_token":"a2","refresh_token":"r2","id_token":"i2"
            })
            self.assertEqual(first.load_profile()["client_id"], "oaiapp_first")
            self.assertEqual(second.load_profile()["client_id"], "oaiapp_second")
            self.assertNotEqual(first.load_profile()["subject"], second.load_profile()["subject"])

    def test_45_normalized_token_response_requires_plan_scopes(self):
        claims={"iss":adapter.ISSUER,"sub":"sub","email":"e@example.com"}
        payload={
            "access_token":"a","refresh_token":"r","id_token":"i","token_type":"Bearer",
            "expires_in":3600,"scope":"openid offline_access"
        }
        with self.assertRaises(ValueError):
            adapter.normalize_token_response(payload, client_id="oaiapp_x", ext_agent_host_id=self.host_id, claims=claims)

    def test_46_cli_uses_importlib_for_hyphenated_adapter_filename(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        self.assertIn("importlib.util.spec_from_file_location", cli)
        self.assertNotIn("from chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite", cli)

    def test_47_cli_persists_issued_registration_before_code_exchange(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        save_pos=cli.index("storage.save_profile_atomic(pending)")
        exchange_pos=cli.index("build_token_exchange_request(", save_pos)
        self.assertLess(save_pos, exchange_pos)
        self.assertIn('"registration_pending": True', cli)

    def test_48_authorization_url_with_id_token_hint_is_sanitized(self):
        url=adapter.build_authorization_url(
            redirect_uri=self.redirect,
            ext_agent_host_id=self.host_id,
            attempt=self.attempt,
            issued_client_id="oaiapp_x",
            retained_id_token="ID_TOKEN_HINT_SENTINEL",
        )
        clean=adapter.sanitize_text(url)
        self.assertNotIn("ID_TOKEN_HINT_SENTINEL", clean)

    def test_49_response_request_has_no_api_key_or_conversation_state(self):
        req=adapter.build_responses_plan_request(
            review_prompt="Review.",
            review_context={"repository":"owner/repo","pr_number":6},
        )
        encoded=json.dumps(req)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("previous_response_id", encoded)
        self.assertNotIn('"conversation"', encoded)
        self.assertFalse(req["store"])
        self.assertTrue(req["stream"])


if __name__ == "__main__":
    unittest.main()
