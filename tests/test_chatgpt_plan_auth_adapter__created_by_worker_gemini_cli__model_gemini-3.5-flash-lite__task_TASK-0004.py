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
        self.assertTrue(adapter.verify_granted_scopes(" ".join(adapter.REQUIRED_SCOPES))[0])
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
                "scopes": list(adapter.REQUIRED_SCOPES),
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
                "scopes": list(adapter.REQUIRED_SCOPES),
            })
            transport = FakeTransport()
            transport.refresh_payload = {
                "access_token": "new_access",
                "refresh_token": "new_refresh",
                "id_token": "new_id",
                "expires_in": 3600,
                "scope": " ".join(adapter.REQUIRED_SCOPES),
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
            base.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"old","access_token":"old_access","id_token":"old_id","scopes":list(adapter.REQUIRED_SCOPES)})
            storage = FailingStorage(base.storage_path, base.protector)
            transport = FakeTransport()
            transport.refresh_payload = {"access_token":"new","refresh_token":"newr","id_token":"newid","expires_in":3600,"scope":" ".join(adapter.REQUIRED_SCOPES)}
            result = adapter.refresh_profile(storage, transport)
            self.assertEqual(result["status"], adapter.BLOCKED_INFRASTRUCTURE_ERROR)
            self.assertEqual(base.load_profile()["refresh_token"], "old")

    def test_23_refresh_lock_serializes_two_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","scopes":list(adapter.REQUIRED_SCOPES)})
            class Rotating(FakeTransport):
                def post_form(self, url, data, headers=None):
                    time.sleep(0.03)
                    current = data["refresh_token"]
                    n = int(current[1:]) + 1
                    return {"access_token":f"a{n}","refresh_token":f"r{n}","id_token":f"i{n}","expires_in":3600,"scope":" ".join(adapter.REQUIRED_SCOPES)}
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
                "scopes":list(adapter.REQUIRED_SCOPES),
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
                "scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0
            })
            transport = FakeTransport()
            transport.refresh_payload = {"access_token":"a1","refresh_token":"r1","expires_in":3600}
            profile, status = adapter.ensure_fresh_profile(storage, transport)
            self.assertEqual(status, "PROFILE_READY")
            self.assertEqual(profile["access_token"], "a1")

    def _snapshot(self):
        return {
            "task_id": "TASK-0004",
            "repository": "pddkalyan/project-agent-orchestrator",
            "pr_number": 6,
            "base_sha": "a" * 40,
            "candidate_sha": "b" * 40,
            "reviewer_context_version": "v1",
            "changed_files": ["scripts/x.py"],
            "required_ci": [{
                "workflow": "offline",
                "run_id": 1,
                "head_sha": "b" * 40,
                "result": "PASS",
                "digest": "sha256:" + "c" * 64,
            }],
        }

    def _live_storage(self, tmp):
        storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
        storage.save_profile_atomic({
            "profile_label": "default",
            "client_id": "oaiapp_x",
            "subject": "sub-1",
            "refresh_token": "r0",
            "access_token": "a0",
            "id_token": "i0",
            "token_type": "Bearer",
            "scopes": list(adapter.REQUIRED_SCOPES),
            "expires_at": int(time.time()) + 3600,
            "session_state": "ACTIVE",
            "profile_version": 1,
        })
        return storage

    def _allowance(self):
        now = time.time()
        return {
            "billing_mode": "ZERO_SPEND_PLAN",
            "separately_billed": False,
            "credits_enabled": False,
            "remaining_requests": 3,
            "profile_id": "default",
            "subject": "sub-1",
            "client_id": "oaiapp_x",
            "observed_at": now - 1,
            "expires_at": now + 60,
        }

    def test_40_single_pipeline_applies_task0003_and_persistence(self):
        snapshot = self._snapshot()
        digest = adapter.bridge.sha256_json(snapshot)
        class LiveTransport(FakeTransport):
            def __init__(self):
                super().__init__()
                self.models_payload = {"models":[{"slug":"gpt-6-astra","visibility":"list"}]}
                self.stream_calls = 0
            def stream_sse(self, url, payload, headers):
                self.stream_calls += 1
                verdict = {"status":"APPROVED","reviewed_sha":"b"*40,"review_snapshot_digest":digest,"findings":[]}
                yield {"type":"response.output_text.delta","delta":json.dumps(verdict)}
                yield {"type":"response.completed"}
        with tempfile.TemporaryDirectory() as tmp:
            storage = self._live_storage(tmp)
            t = LiveTransport()
            result = adapter.run_streamed_review(
                storage=storage,
                plan_allowance_evidence=self._allowance(),
                activation_policy={"reviewer_enabled":True,"zero_extra_spend_confirmed":True},
                transport=t,
                review_prompt="Review.",
                expected_snapshot=snapshot,
                review_request=deepcopy(snapshot),
            )
            self.assertEqual(result["status"], "APPROVED")
            self.assertTrue(result["completed"])
            self.assertEqual(result["persistence"]["action"], "PERSIST_ONCE")
            self.assertEqual(t.stream_calls, 1)

    def test_41_invalid_snapshot_prevents_inference(self):
        class Never(FakeTransport):
            def stream_sse(self, *args, **kwargs):
                raise AssertionError("must not infer")
        with tempfile.TemporaryDirectory() as tmp:
            storage = self._live_storage(tmp)
            t = Never(); t.models_payload={"models":[{"slug":"gpt-6-astra","visibility":"list"}]}
            bad = self._snapshot(); bad["candidate_sha"] = "d" * 40
            result = adapter.run_streamed_review(
                storage=storage, plan_allowance_evidence=self._allowance(),
                activation_policy={"reviewer_enabled":True,"zero_extra_spend_confirmed":True},
                transport=t, review_prompt="Review.",
                expected_snapshot=self._snapshot(), review_request=bad,
            )
            self.assertEqual(result["status"], adapter.BLOCKED_INVALID_RESPONSE)

    def test_42_expired_or_wrong_account_allowance_prevents_inference(self):
        class Never(FakeTransport):
            def __init__(self):
                super().__init__(); self.models_payload={"models":[{"slug":"gpt-6-astra","visibility":"list"}]}
            def stream_sse(self, *args, **kwargs):
                raise AssertionError("must not infer")
        with tempfile.TemporaryDirectory() as tmp:
            storage=self._live_storage(tmp); t=Never()
            for mutate in ("expired","wrong"):
                evidence=self._allowance()
                if mutate=="expired": evidence["expires_at"]=time.time()-1
                else: evidence["subject"]="other"
                result=adapter.run_streamed_review(
                    storage=storage,plan_allowance_evidence=evidence,
                    activation_policy={"reviewer_enabled":True,"zero_extra_spend_confirmed":True},
                    transport=t,review_prompt="Review.",expected_snapshot=self._snapshot(),review_request=self._snapshot())
                self.assertEqual(result["status"], adapter.BLOCKED_PLAN_ALLOWANCE)

    def test_43_legacy_caller_supplied_profile_or_catalog_cannot_authorize(self):
        class Never:
            def stream_sse(self, *args, **kwargs):
                raise AssertionError("must not infer")
        result=adapter.run_streamed_review(
            profile={"access_token":"x"}, model_catalog={"user_authorized":True,"authorized_models":["gpt-6-astra"]},
            activation_policy={"reviewer_enabled":True,"zero_extra_spend_confirmed":True},
            transport=Never(), review_prompt="Review.", review_context={"task_id":"TASK-0004"})
        self.assertEqual(result["status"], adapter.BLOCKED_INVALID_RESPONSE)

    def test_44_stale_verdict_is_blocked_after_transport(self):
        snapshot=self._snapshot()
        class T(FakeTransport):
            def __init__(self):
                super().__init__(); self.models_payload={"models":[{"slug":"gpt-6-astra","visibility":"list"}]}
            def stream_sse(self,*args,**kwargs):
                verdict={"status":"APPROVED","reviewed_sha":"e"*40,"review_snapshot_digest":adapter.bridge.sha256_json(snapshot),"findings":[]}
                yield {"type":"response.output_text.delta","delta":json.dumps(verdict)}
                yield {"type":"response.completed"}
        with tempfile.TemporaryDirectory() as tmp:
            result=adapter.run_streamed_review(
                storage=self._live_storage(tmp),plan_allowance_evidence=self._allowance(),
                activation_policy={"reviewer_enabled":True,"zero_extra_spend_confirmed":True},
                transport=T(),review_prompt="Review.",expected_snapshot=snapshot,review_request=deepcopy(snapshot))
            self.assertEqual(result["status"], adapter.bridge.BLOCKED_INVALID_VERDICT)

    def test_45_activation_gates_prevent_transport(self):
        class Never:
            def stream_sse(self,*args,**kwargs): raise AssertionError("must not infer")
        for policy in (
            {"reviewer_enabled":False,"zero_extra_spend_confirmed":True},
            {"reviewer_enabled":True,"zero_extra_spend_confirmed":False},
        ):
            result=adapter.run_streamed_review(
                activation_policy=policy,transport=Never(),review_prompt="Review.")
            self.assertEqual(result["status"], adapter.BLOCKED_PLAN_ALLOWANCE)

    def test_46_refresh_error_codes_fail_closed_as_auth(self):
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

    def test_47_subscription_route_capability_errors_are_not_auth_or_verdicts(self):
        for code in [
            "subscription_sharing_unsupported_capability",
            "subscription_sharing_route_not_supported",
        ]:
            status = adapter.map_transport_error(code)
            self.assertEqual(status, adapter.BLOCKED_INVALID_RESPONSE)
            self.assertNotIn(status, ("APPROVED", "REJECTED"))

    def test_48_profile_files_keep_account_registrations_separate(self):
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

    def test_49_normalized_token_response_requires_plan_scopes(self):
        claims={"iss":adapter.ISSUER,"sub":"sub","email":"e@example.com"}
        payload={
            "access_token":"a","refresh_token":"r","id_token":"i","token_type":"Bearer",
            "expires_in":3600,"scope":"openid offline_access"
        }
        with self.assertRaises(ValueError):
            adapter.normalize_token_response(payload, client_id="oaiapp_x", ext_agent_host_id=self.host_id, claims=claims)

    def test_50_cli_uses_importlib_for_hyphenated_adapter_filename(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        self.assertIn("importlib.util.spec_from_file_location", cli)
        self.assertNotIn("from chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite", cli)

    def test_51_cli_persists_issued_registration_before_code_exchange(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        save_pos=cli.index("storage.save_registration_atomic(registration)")
        exchange_pos=cli.index("build_token_exchange_request(", save_pos)
        self.assertLess(save_pos, exchange_pos)
        self.assertIn('"registration_pending": True', cli)
        self.assertNotIn("storage.save_profile_atomic(pending)", cli)

    def test_52_authorization_url_with_id_token_hint_is_sanitized(self):
        url=adapter.build_authorization_url(
            redirect_uri=self.redirect,
            ext_agent_host_id=self.host_id,
            attempt=self.attempt,
            issued_client_id="oaiapp_x",
            retained_id_token="ID_TOKEN_HINT_SENTINEL",
        )
        clean=adapter.sanitize_text(url)
        self.assertNotIn("ID_TOKEN_HINT_SENTINEL", clean)

    def test_53_response_request_has_no_api_key_or_conversation_state(self):
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


    def test_54_private_key_block_and_auth_code_are_redacted(self):
        text = (
            "ordinary before\n"
            "auth_code: AUTH_CODE_SENTINEL\n"
            "-----BEGIN PRIVATE KEY-----\n"
            "PRIVATE_KEY_SENTINEL\n"
            "-----END PRIVATE KEY-----\n"
            "ordinary after"
        )
        clean = adapter.sanitize_text(text)
        self.assertNotIn("AUTH_CODE_SENTINEL", clean)
        self.assertNotIn("PRIVATE_KEY_SENTINEL", clean)
        self.assertIn("ordinary before", clean)
        self.assertIn("ordinary after", clean)

    def test_55_refresh_invalid_grant_maps_to_auth_required(self):
        class InvalidGrantTransport:
            def post_form(self, *args, **kwargs):
                raise adapter.SafeTransportError(http_status=400, code="invalid_grant", category="http")
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({
                "client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0",
                "scopes":list(adapter.REQUIRED_SCOPES)
            })
            result = adapter.refresh_profile(storage, InvalidGrantTransport())
            self.assertEqual(result["status"], adapter.BLOCKED_AUTH_REQUIRED)

    def test_56_cli_supports_separate_profile_labels(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        self.assertIn('--profile', cli)
        self.assertIn('DEFAULT_PROFILES_DIR', cli)
        self.assertIn('sub.add_parser("profiles")', cli)

    def test_57_cli_sign_in_is_user_initiated_and_ci_blocked(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("CI"', cli)
        self.assertIn('not sys.stdin.isatty()', cli)
        self.assertIn('webbrowser.open(url)', cli)

    def test_58_no_secret_or_paid_api_identifiers_in_public_self_check(self):
        encoded=json.dumps(adapter.self_check())
        self.assertNotIn("access_token", encoded)
        self.assertNotIn("refresh_token", encoded)
        self.assertNotIn("OPENAI_API_KEY", encoded)
        self.assertIn('"paid_fallback": false', encoded.lower())


    def test_59_non_bearer_token_type_is_rejected(self):
        claims={"iss":adapter.ISSUER,"sub":"sub","email":"e@example.com"}
        payload={
            "access_token":"a","refresh_token":"r","id_token":"i","token_type":"MAC",
            "expires_in":3600,"scope":"openid offline_access chatgpt.tokens.use.direct"
        }
        with self.assertRaises(ValueError):
            adapter.normalize_token_response(payload, client_id="oaiapp_x", ext_agent_host_id=self.host_id, claims=claims)

    def test_60_expired_profile_refreshes_even_before_earliest_refresh_at(self):
        now=1000
        self.assertTrue(adapter.needs_refresh({"expires_at":999,"earliest_refresh_at":5000}, now=now))


    def test_61_multiline_sensitive_diagnostic_is_fully_redacted(self):
        text = "refresh_token: |\n  VALUE_ONE\n  VALUE_TWO\npublic: ok"
        clean = adapter.sanitize_text(text)
        self.assertNotIn("VALUE_ONE", clean)
        self.assertNotIn("VALUE_TWO", clean)
        self.assertIn("public: ok", clean)

    def test_62_wrong_account_or_stale_allowance_blocks(self):
        now = time.time()
        profile = {"profile_label":"default","subject":"sub-1","client_id":"oaiapp_x"}
        base = {
            "billing_mode":"ZERO_SPEND_PLAN","separately_billed":False,"credits_enabled":False,
            "remaining_requests":1,"profile_id":"default","subject":"sub-1","client_id":"oaiapp_x",
            "observed_at":now-1,"expires_at":now+60,
        }
        self.assertTrue(adapter._validate_allowance_evidence(base, profile)[0])
        for field, value in (("subject","other"),("client_id","other"),("profile_id","other"),("remaining_requests",0),("expires_at",now-1)):
            bad = dict(base); bad[field] = value
            self.assertFalse(adapter._validate_allowance_evidence(bad, profile)[0])

    def test_63_version_guard_prevents_stale_signin_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            initial = {"profile_label":"p","client_id":"oaiapp_x","subject":"s","refresh_token":"r1","access_token":"a1","id_token":"i1","profile_version":1}
            storage.save_profile_atomic(initial)
            newer = dict(initial); newer["refresh_token"]="r2"; newer["profile_version"]=2
            storage.save_profile_atomic(newer)
            stale = dict(initial); stale["refresh_token"]="r0"
            self.assertFalse(storage.replace_profile_if_version(expected_version=1, profile=stale, expected_subject="s", expected_client_id="oaiapp_x"))
            self.assertEqual(storage.load_profile()["refresh_token"], "r2")

    def test_64_invalid_refresh_payload_blocks_and_does_not_reuse_old_rotating_value(self):
        class T:
            def post_form(self, *args, **kwargs):
                return {"access_token":"a2","refresh_token":"r2","token_type":"MAC","expires_in":3600,"scope":" ".join(adapter.REQUIRED_SCOPES)}
        with tempfile.TemporaryDirectory() as tmp:
            storage = adapter.HostCredentialStorage(Path(tmp) / "profile.json", FakeProtector())
            storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r1","access_token":"a1","id_token":"i1","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0,"session_state":"ACTIVE"})
            result = adapter.refresh_profile(storage, T())
            self.assertEqual(result["status"], adapter.BLOCKED_AUTH_REQUIRED)
            saved = storage.load_profile()
            self.assertEqual(saved["session_state"], "BLOCKED_REFRESH_INVALID")
            self.assertEqual(saved.get("refresh_token"), "r2")
            self.assertNotEqual(saved.get("refresh_token"), "r1")

    def test_65_bad_lifetime_error_never_echoes_supplied_value(self):
        claims={"iss":adapter.ISSUER,"sub":"s","email":"e@example.com"}
        payload={"access_token":"a","refresh_token":"r","id_token":"i","token_type":"Bearer","expires_in":"VALUE_BAD","scope":" ".join(adapter.REQUIRED_SCOPES)}
        with self.assertRaisesRegex(ValueError, "^invalid token lifetime$"):
            adapter.normalize_token_response(payload,client_id="oaiapp_x",ext_agent_host_id=self.host_id,claims=claims)

    def test_66_lock_setup_failure_releases_process_mutex(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = adapter.FileLock(Path(tmp) / "missing" / "x.lock", timeout_seconds=0.1)
            original = Path.mkdir
            try:
                Path.mkdir = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic"))
                with self.assertRaises(OSError):
                    with lock:
                        pass
            finally:
                Path.mkdir = original
            self.assertTrue(adapter.FileLock._process_lock.acquire(timeout=0.1))
            adapter.FileLock._process_lock.release()

    def test_67_cli_uses_separate_registration_and_safe_exception_boundary(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        self.assertIn("save_registration_atomic(registration)", cli)
        self.assertNotIn("save_profile_atomic(pending)", cli)
        self.assertIn("def _safe_main()", cli)
        self.assertNotIn('"message": str(exc)', cli)

    def test_68_legacy_live_arguments_fail_closed_without_transport(self):
        class Never:
            def stream_sse(self,*args,**kwargs):
                raise AssertionError("transport must not be called")
        result = adapter.run_streamed_review(
            profile={"access_token":"x"},
            model_catalog={"user_authorized":True,"authorized_models":["gpt-6-astra"]},
            activation_policy={"reviewer_enabled":True,"zero_extra_spend_confirmed":True},
            transport=Never(),
            review_prompt="Review.",
            review_context={"task_id":"TASK-0004"},
        )
        self.assertEqual(result["status"], adapter.BLOCKED_INVALID_RESPONSE)


    def test_69_refresh_timeout_persists_in_progress_and_never_resubmits_old_value(self):
        class T:
            def __init__(self): self.calls=[]
            def post_form(self, url, data, headers=None):
                self.calls.append(data["refresh_token"])
                raise adapter.SafeTransportError(category="network")
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"profile.json"
            storage=adapter.HostCredentialStorage(path, FakeProtector())
            storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0,"session_state":"ACTIVE"})
            t=T()
            first=adapter.ensure_fresh_profile(storage,t)
            self.assertEqual(first[1], adapter.BLOCKED_NETWORK_ERROR)
            self.assertEqual(storage.load_profile()["session_state"], "REFRESH_IN_PROGRESS")
            restarted=adapter.HostCredentialStorage(path, FakeProtector())
            second=adapter.ensure_fresh_profile(restarted,t)
            self.assertEqual(second[1], adapter.BLOCKED_AUTH_REQUIRED)
            self.assertEqual(t.calls, ["r0"])

    def test_70_malformed_refresh_response_stays_durably_blocked(self):
        class T:
            def __init__(self): self.calls=0
            def post_form(self,*args,**kwargs):
                self.calls+=1
                return None
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"profile.json"
            storage=adapter.HostCredentialStorage(path, FakeProtector())
            storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0,"session_state":"ACTIVE"})
            t=T()
            result=adapter.refresh_profile(storage,t)
            self.assertEqual(result["status"], adapter.BLOCKED_INVALID_RESPONSE)
            self.assertEqual(storage.load_profile()["session_state"], "REFRESH_IN_PROGRESS")
            self.assertEqual(adapter.ensure_fresh_profile(storage,t)[1], adapter.BLOCKED_AUTH_REQUIRED)
            self.assertEqual(t.calls,1)

    def test_71_failed_replacement_write_leaves_pre_dispatch_block_on_disk(self):
        class FailSecond(adapter.HostCredentialStorage):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs); self.writes=0
            def save_profile_atomic(self,profile):
                self.writes+=1
                if self.writes==2:
                    raise OSError("synthetic")
                return super().save_profile_atomic(profile)
        class T:
            def post_form(self,*args,**kwargs):
                return {"access_token":"a2","refresh_token":"r2","expires_in":3600,"scope":" ".join(adapter.REQUIRED_SCOPES)}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"profile.json"
            base=adapter.HostCredentialStorage(path,FakeProtector())
            base.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0,"session_state":"ACTIVE"})
            storage=FailSecond(path,FakeProtector())
            result=adapter.refresh_profile(storage,T())
            self.assertEqual(result["status"],adapter.BLOCKED_INFRASTRUCTURE_ERROR)
            self.assertEqual(base.load_profile()["session_state"],"REFRESH_IN_PROGRESS")

    def test_72_explicit_invalid_refresh_scopes_never_inherit_old_grant(self):
        values=["","   ",None,[],{}, "offline_access chatgpt.tokens.use.direct"]
        class T:
            def __init__(self,scope): self.scope=scope
            def post_form(self,*args,**kwargs):
                return {"access_token":"a2","refresh_token":"r2","expires_in":3600,"scope":self.scope}
        for value in values:
            with self.subTest(scope=value), tempfile.TemporaryDirectory() as tmp:
                storage=adapter.HostCredentialStorage(Path(tmp)/"profile.json",FakeProtector())
                storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0,"session_state":"ACTIVE"})
                result=adapter.refresh_profile(storage,T(value))
                self.assertEqual(result["status"],adapter.BLOCKED_AUTH_REQUIRED)
                self.assertNotEqual(storage.load_profile().get("session_state"),"ACTIVE")

    def test_73_absent_refresh_scope_legitimately_preserves_previous_full_grant(self):
        class T:
            def post_form(self,*args,**kwargs):
                return {"access_token":"a2","refresh_token":"r2","expires_in":3600}
        with tempfile.TemporaryDirectory() as tmp:
            storage=adapter.HostCredentialStorage(Path(tmp)/"profile.json",FakeProtector())
            storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":0,"session_state":"ACTIVE"})
            result=adapter.refresh_profile(storage,T())
            self.assertEqual(result["status"],"REFRESHED")
            self.assertEqual(set(storage.load_profile()["scopes"]),set(adapter.REQUIRED_SCOPES))

    def test_74_nonfinite_allowance_timestamps_block(self):
        profile={"profile_label":"default","subject":"s","client_id":"c"}
        now=time.time()
        base={"billing_mode":"ZERO_SPEND_PLAN","separately_billed":False,"credits_enabled":False,"remaining_requests":1,"profile_id":"default","subject":"s","client_id":"c","observed_at":now-1,"expires_at":now+30}
        invalid=[float("nan"),float("inf"),float("-inf"),True]
        for value in invalid:
            for field in ("observed_at","expires_at"):
                bad=dict(base); bad[field]=value
                self.assertFalse(adapter._validate_allowance_evidence(bad,profile,now=now)[0])
        reversed_order=dict(base); reversed_order["expires_at"]=reversed_order["observed_at"]
        self.assertFalse(adapter._validate_allowance_evidence(reversed_order,profile,now=now)[0])

    def test_75_nonfinite_session_expiry_blocks_without_refresh(self):
        class Never:
            def post_form(self,*args,**kwargs): raise AssertionError("refresh must not run")
        for value in (float("nan"),float("inf"),float("-inf"),True):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                storage=adapter.HostCredentialStorage(Path(tmp)/"profile.json",FakeProtector())
                storage.save_profile_atomic({"client_id":"oaiapp_x","refresh_token":"r0","access_token":"a0","id_token":"i0","token_type":"Bearer","scopes":list(adapter.REQUIRED_SCOPES),"expires_at":value,"session_state":"ACTIVE"})
                profile,status=adapter.ensure_fresh_profile(storage,Never())
                self.assertIsNone(profile)
                self.assertEqual(status,adapter.BLOCKED_AUTH_REQUIRED)

    def test_76_cli_loads_pending_registration_for_retry(self):
        cli=(ROOT / "scripts" / "chatgpt_plan_auth_cli__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py").read_text(encoding="utf-8")
        self.assertIn("registration = storage.load_registration()",cli)
        self.assertIn('registration.get("client_id") if registration else None',cli)
        self.assertIn("Saved registration does not match this profile or host.",cli)

    def test_77_malformed_stream_events_return_blocked_not_exception(self):
        bad_events=[None,1,"x",[],{"foo":"bar"},{"type":"response.output_text.delta","delta":None}]
        for event in bad_events:
            with self.subTest(event=event):
                result=adapter.assemble_stream([event])
                self.assertEqual(result["status"],adapter.BLOCKED_INVALID_RESPONSE)

    def test_78_transport_review_contains_malformed_stream_failures(self):
        class T:
            def stream_sse(self,*args,**kwargs):
                yield None
        result=adapter._transport_review(profile={"access_token":"x"},transport=T(),review_prompt="Review.",review_context={"task_id":"TASK-0004"})
        self.assertEqual(result["status"],adapter.BLOCKED_INVALID_RESPONSE)
        self.assertFalse(result["completed"])


if __name__ == "__main__":
    unittest.main()
