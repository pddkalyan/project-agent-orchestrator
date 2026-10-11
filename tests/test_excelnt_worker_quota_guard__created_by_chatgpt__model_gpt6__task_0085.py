"""Offline deterministic safety tests for Issue #85 Excelnt A/B workers.

No network, credentials, paid provider usage, or production actor calls.
"""
from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.excelnt_worker_quota_guard__created_by_chatgpt__model_gpt6__task_0085 import (FeedUnavailable, GuardBlocked, QuotaSnapshot, StateCorrupt,
                          TaskCheckpoint, WorkerBusy, WorkerQuotaGuard)

NOW = datetime(2026, 10, 11, 7, 30, 0, tzinfo=timezone.utc)


def iso(ts):
    return ts.isoformat().replace('+00:00', 'Z')


def snapshot(worker='A', used5=100, usedweek=100, limit5=600, limitweek=4200,
             observed=NOW, reset5=NOW+timedelta(hours=4),
             resetweek=NOW+timedelta(days=6), paid_off=True, topup_off=True):
    return QuotaSnapshot(worker=worker, observed_at=iso(observed),
                         five_hour_reset_at=iso(reset5), weekly_reset_at=iso(resetweek),
                         five_hour_used=used5, five_hour_limit=limit5,
                         weekly_used=usedweek, weekly_limit=limitweek,
                         paid_overage_disabled=paid_off, auto_topup_disabled=topup_off)


class FakeFeed:
    def __init__(self, **snapshots):
        self.snapshots=snapshots
        self.calls=[]
    def read_snapshot(self, worker):
        self.calls.append(worker)
        value=self.snapshots.get(worker)
        if isinstance(value, BaseException): raise value
        if value is None: raise FeedUnavailable('not connected')
        return value


def cp(task='TASK-85', branch='candidate/excelnt/A', sha='a'*40):
    return TaskCheckpoint(task,branch,sha,'offline tests passed','continue scoped unit testing')


class OfflineGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.feed=FakeFeed(A=snapshot(),B=snapshot(worker='B',used5=500))
        self.now=NOW
        self.guard=WorkerQuotaGuard(self.root, self.feed, clock=lambda:self.now)

    def preflight(self, request='req-1',worker='A',quota=15, checkpoint=None):
        return self.guard.request_preflight(worker,request_id=request,
            checkpoint=checkpoint or cp(branch='candidate/excelnt/'+worker),worst_case_units=quota)

    def test_no_provider_feed_denies_and_checkpoints(self):
        g=WorkerQuotaGuard(self.root,clock=lambda:self.now)
        with self.assertRaises(FeedUnavailable):
            g.request_preflight('A',request_id='req-a',checkpoint=cp(),worst_case_units=1)
        state=g.read_status('A')
        self.assertEqual(state['status'],'UNKNOWN')
        self.assertEqual(state['checkpoint']['head_sha'],'a'*40)
        self.assertIsNone(state['pending_request_id'])

    def test_preflight_writes_intent_before_external_request(self):
        self.assertEqual(self.preflight(),'APPROVED')
        s=self.guard.read_status('A')
        self.assertEqual(s['status'],'IN_FLIGHT')
        self.assertEqual(s['pending_request_id'],'req-1')
        self.assertEqual(s['checkpoint']['branch'],'candidate/excelnt/A')
        self.assertTrue((self.root/'worker_A.json').exists())
        self.assertFalse((self.root/'worker_A.lock').exists())

    def test_finish_success_clears_intent_but_duplicate_is_blocked(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='SUCCESS')
        self.assertEqual(self.guard.read_status('A')['status'],'READY')
        with self.assertRaises(GuardBlocked):self.preflight()
        self.assertEqual(self.preflight('req-2'),'APPROVED')

    def test_warning_at_eighty_and_stop_at_ninety_percent(self):
        self.feed.snapshots['A']=snapshot(used5=480)
        self.assertEqual(self.preflight(quota=5),'WARNING')
        self.guard.record_outcome('A',request_id='req-1',outcome='SUCCESS')
        self.feed.snapshots['A']=snapshot(used5=540)
        with self.assertRaises(GuardBlocked):self.preflight('req-2',quota=5)
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')

    def test_headroom_accounts_for_worst_case_request(self):
        self.feed.snapshots['A']=snapshot(used5=530)
        with self.assertRaises(GuardBlocked):self.preflight(quota=15)
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')

    def test_weekly_limit_also_enforced_independently(self):
        self.feed.snapshots['A']=snapshot(usedweek=3780)
        with self.assertRaises(GuardBlocked):self.preflight(quota=1)
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')

    def test_midtask_quota_refusal_pauses_and_preserves_checkpoint(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='QUOTA_REFUSED')
        s=self.guard.read_status('A')
        self.assertEqual(s['status'],'WAITING_FOR_RESET')
        self.assertIsNone(s['pending_request_id'])
        self.assertEqual(s['checkpoint']['next_action'],'continue scoped unit testing')
        with self.assertRaises(GuardBlocked):self.preflight('req-2')
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')

    def test_quota_wait_persists_through_telemetry_outage_and_same_window(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='QUOTA_REFUSED')
        self.now=NOW+timedelta(minutes=8)
        self.feed.snapshots['A']=ConnectionError('telemetry outage')
        with self.assertRaises(FeedUnavailable):self.preflight('req-2')
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')
        self.feed.snapshots['A']=snapshot(used5=0,observed=self.now)
        with self.assertRaises(GuardBlocked):self.preflight('req-3')
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')

    def test_real_refresh_can_resume_same_worker_when_confirmed(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='QUOTA_REFUSED')
        self.now=NOW+timedelta(hours=5)
        self.feed.snapshots['A']=snapshot(used5=0,usedweek=120,
            observed=self.now,reset5=self.now+timedelta(hours=5))
        self.assertEqual(self.preflight('req-2'),'APPROVED')
        self.assertEqual(self.guard.read_status('A')['status'],'IN_FLIGHT')

    def test_same_window_low_fake_balance_does_not_unlock_wait(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='QUOTA_REFUSED')
        self.now=NOW+timedelta(minutes=7)
        self.feed.snapshots['A']=snapshot(used5=0, observed=self.now)
        with self.assertRaises(GuardBlocked):self.preflight('req-2')

    def test_after_reset_must_be_fresh(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='QUOTA_REFUSED')
        self.now=NOW+timedelta(hours=5)
        self.feed.snapshots['A']=snapshot(used5=0,
            observed=NOW,reset5=NOW+timedelta(hours=10))
        with self.assertRaises(FeedUnavailable):self.preflight('req-2')

    def test_missing_or_invalid_provider_report_blocks(self):
        self.feed.snapshots['A']=None
        with self.assertRaises(FeedUnavailable):self.preflight()
        self.feed.snapshots['A']=snapshot(worker='B')
        with self.assertRaises(FeedUnavailable):self.preflight('req-2')
        self.feed.snapshots['A']={'worker':'A'}
        with self.assertRaises(FeedUnavailable):self.preflight('req-3')

    def test_stale_future_naive_expired_and_impossible_time(self):
        variants=[
           snapshot(observed=NOW-timedelta(minutes=6)),
           snapshot(observed=NOW+timedelta(minutes=2)),
           snapshot(reset5=NOW),
           snapshot(resetweek=NOW),
           dataclasses.replace(snapshot(),observed_at='2026-10-11T07:30:00'),
           dataclasses.replace(snapshot(),five_hour_reset_at='not-date'),
        ]
        for n,s in enumerate(variants):
            with self.subTest(n=n):
                self.feed.snapshots['A']=s
                with self.assertRaises(FeedUnavailable):self.preflight(f'req-{n}')

    def test_no_overage_or_topup_proof_no_request(self):
        for s in [snapshot(paid_off=False),snapshot(topup_off=False)]:
            self.feed.snapshots['A']=s
            with self.assertRaises(GuardBlocked):self.preflight('req-b')
            self.assertEqual(self.guard.read_status('A')['status'],'AUTH_ERROR')

    def test_bad_usage_counters_boolean_negative_nan_inf_missing(self):
        variants=[
         snapshot(used5=-1),snapshot(limit5=0),snapshot(usedweek=5000),
         snapshot(used5=True),snapshot(used5=float('nan')),
         snapshot(used5=float('inf')),
        ]
        for i,s in enumerate(variants):
            with self.subTest(i=i):
                self.feed.snapshots['A']=s
                with self.assertRaises(GuardBlocked):self.preflight(f'req-{i}')

    def test_separate_accounts_leases_and_quotas(self):
        self.assertEqual(self.preflight('req-A',worker='A'),'APPROVED')
        self.assertEqual(self.preflight('req-B',worker='B',quota=10),'WARNING')
        self.assertEqual(self.guard.read_status('A')['pending_request_id'],'req-A')
        self.assertEqual(self.guard.read_status('B')['pending_request_id'],'req-B')
        self.guard.record_outcome('A',request_id='req-A',outcome='QUOTA_REFUSED')
        self.assertEqual(self.guard.read_status('A')['status'],'WAITING_FOR_RESET')
        self.assertEqual(self.guard.read_status('B')['status'],'IN_FLIGHT')

    def test_busy_lease_fails_closed_even_when_other_worker_active(self):
        self.root.joinpath('worker_A.lock').mkdir()
        with self.assertRaises(WorkerBusy):self.preflight()
        self.assertEqual(self.preflight('req-b',worker='B'),'WARNING')

    def test_recover_interrupted_inflight_never_auto_replays(self):
        self.preflight()
        g2=WorkerQuotaGuard(self.root,self.feed,clock=lambda:self.now)
        recovered=g2.recover_after_restart('A')
        self.assertEqual(recovered['status'],'NEEDS_RECONCILIATION')
        self.assertEqual(recovered['pending_request_id'],'req-1')
        with self.assertRaises(GuardBlocked):g2.request_preflight('A',request_id='req-2',checkpoint=cp(),worst_case_units=1)

    def test_unknown_and_rate_limit_are_ambiguous_not_autoretried(self):
        for outcome in ('RATE_LIMITED','UNKNOWN'):
            with self.subTest(outcome=outcome):
                with tempfile.TemporaryDirectory() as directory:
                    g=WorkerQuotaGuard(Path(directory),self.feed,clock=lambda:self.now)
                    g.request_preflight('A',request_id='req-1',checkpoint=cp(),worst_case_units=1)
                    g.record_outcome('A',request_id='req-1',outcome=outcome)
                    self.assertEqual(g.read_status('A')['status'],'NEEDS_RECONCILIATION')
                    with self.assertRaises(GuardBlocked):g.request_preflight('A',request_id='req-2',checkpoint=cp(),worst_case_units=1)

    def test_auth_refused_is_not_quota_reset(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='AUTH_REFUSED')
        self.assertEqual(self.guard.read_status('A')['status'],'AUTH_ERROR')
        self.assertIsNone(self.guard.read_status('A')['pending_request_id'])

    def test_unexpected_request_completion_rejected(self):
        with self.assertRaises(GuardBlocked):self.guard.record_outcome('A',request_id='not-running',outcome='SUCCESS')
        self.preflight()
        with self.assertRaises(GuardBlocked):self.guard.record_outcome('A',request_id='req-unknown',outcome='SUCCESS')
        with self.assertRaises(GuardBlocked):self.guard.record_outcome('A',request_id='req-1',outcome='invalid')

    def test_corrupted_json_is_never_clobbered(self):
        path=self.root/'worker_A.json'
        path.write_bytes(b'{"version": 1, BROKEN')
        before=path.read_bytes()
        with self.assertRaises(StateCorrupt):self.preflight()
        with self.assertRaises(StateCorrupt):self.guard.recover_after_restart('A')
        self.assertEqual(path.read_bytes(),before)

    def test_missing_checkpoint_field_and_unknown_status_fail_closed(self):
        self.preflight()
        path=self.root/'worker_A.json'
        for key,value in [('status',['IN_FLIGHT']),('checkpoint',{'task_id':'TASK-85'})]:
            state=json.loads(path.read_text())
            state[key]=value
            raw=json.dumps(state).encode()
            path.write_bytes(raw)
            with self.assertRaises(StateCorrupt):self.guard.read_status('A')
            self.assertEqual(path.read_bytes(),raw)
            path.write_bytes(json.dumps({"version":1}).encode())
        with self.assertRaises(StateCorrupt):self.guard.read_status('A')

    def test_atomic_write_failure_preserves_prior_checkpoint(self):
        self.guard.update_checkpoint('A',cp())
        path=self.root/'worker_A.json'
        old=path.read_bytes()
        with patch('scripts.excelnt_worker_quota_guard__created_by_chatgpt__model_gpt6__task_0085.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.guard.update_checkpoint('A',
                dataclasses.replace(cp(),next_action='do another unit test'))
        self.assertEqual(path.read_bytes(),old)
        self.assertFalse((self.root/'worker_A.lock').exists())
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_stale_provider_403_auth_and_429_distinct_states(self):
        self.feed.snapshots['A']=PermissionError('403 blocked')
        with self.assertRaises(FeedUnavailable):self.preflight()
        self.assertEqual(self.guard.read_status('A')['status'],'UNKNOWN')
        self.feed.snapshots['A']=ConnectionError('429 limit')
        with self.assertRaises(FeedUnavailable):self.preflight('req-2')
        self.assertEqual(self.guard.read_status('A')['status'],'UNKNOWN')

    def test_task_cannot_be_silently_switched_between_requests(self):
        self.preflight()
        self.guard.record_outcome('A',request_id='req-1',outcome='SUCCESS')
        with self.assertRaises(GuardBlocked):self.preflight('req-2',checkpoint=cp(task='OTHER'))
        with self.assertRaises(GuardBlocked):self.preflight('req-3',checkpoint=cp(branch='different'))

    def test_checkpoint_validates_branch_commit_and_control_chars(self):
        for checkpoint in [cp(sha='x'*40),cp(branch='../main'),cp(branch='main/../evil'),
                           cp(task='bad task'),dataclasses.replace(cp(),next_action='\nsecret')]:
            with self.assertRaises(GuardBlocked):self.guard.update_checkpoint('A',checkpoint)

    def test_redacted_status_never_includes_usage_dollars_or_id(self):
        self.preflight()
        public=self.guard.redacted_status('A')
        self.assertEqual(public['worker_alias'],'A')
        self.assertTrue(public['has_ambiguous_request'])
        self.assertNotIn('pending_request_id',public)
        self.assertNotIn('five_hour_used',public)
        self.assertNotIn('api_key',json.dumps(public))

    def test_worker_alias_invalid_and_no_account_rotation(self):
        for alias in ('C','worker-A','',None,'../../A'):
            with self.assertRaises(GuardBlocked):self.guard.read_status(alias)

    def test_previously_completed_request_cannot_replay_after_newer_requests(self):
        self.preflight('req-1')
        self.guard.record_outcome('A',request_id='req-1',outcome='SUCCESS')
        self.preflight('req-2')
        self.guard.record_outcome('A',request_id='req-2',outcome='SUCCESS')
        with self.assertRaises(GuardBlocked):self.preflight('req-1')
        self.assertEqual(len(self.guard.read_status('A')['completed_request_digests']),2)

    def test_durable_request_journal_at_capacity_blocks_and_preserves_state(self):
        import hashlib
        self.guard.update_checkpoint('A',cp())
        state=self.guard.read_status('A')
        state['completed_request_digests'] = [
            hashlib.sha256(f'closed-{i}'.encode()).hexdigest() for i in range(1024)
        ]
        path=self.root/'worker_A.json'
        path.write_text(json.dumps(state))
        initial=path.read_bytes()
        with self.assertRaises(GuardBlocked):self.preflight('req-new')
        self.assertEqual(path.read_bytes(),initial)

    def test_corrupted_journal_cannot_reopen_capacity(self):
        self.preflight()
        state=self.guard.read_status('A')
        state['completed_request_digests']=['invalid-entry']
        path=self.root/'worker_A.json'
        raw=json.dumps(state).encode()
        path.write_bytes(raw)
        with self.assertRaises(StateCorrupt):self.guard.recover_after_restart('A')
        self.assertEqual(path.read_bytes(),raw)

    def test_no_actual_requests_or_private_credentials_in_guard(self):
        source=(Path(__file__).resolve().parents[1] / 'scripts' /
            'excelnt_worker_quota_guard__created_by_chatgpt__model_gpt6__task_0085.py').read_text()
        for prohibited in ('urllib.request','requests.post','api.excelnt.ai','EXCELNT_API_KEY','top_up('):
            self.assertNotIn(prohibited,source)


if __name__=='__main__':unittest.main()
