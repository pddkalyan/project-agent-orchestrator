"""Regression tests for fail-closed Windows Antigravity installer; no host files modified."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap_antigravity_bridge as mod


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.ws = self.root / 'workspace'
        self.ws.mkdir()
        self.state = self.home / '.antigravity_bridge_state'
        self.state.mkdir(parents=True)
        self.sidecar = self.home / '.gemini' / 'config' / 'sidecars' / 'github_bridge'
        self.sidecar.mkdir(parents=True)
        self.source = self.sidecar / 'bridge.py'
        self.source.write_text('ORIGINAL STATUS-ONLY SIDECAR', encoding='utf-8')
        (self.sidecar / 'sidecar.json').write_text(json.dumps({'command':'python','args':['bridge.py']}))
        self.config = self.state / 'config.json'
        self.config.write_text(json.dumps({'workspace_dir':str(self.ws), 'conversation_id':'private-target',
            'dispatch_enabled':False, 'ping_enabled':False, 'allow_dirty_continue':False, 'status_enabled':True}))
        self.safe_source = b'class AntigravityRemoteBridge:\n    pass\n'
        self.pin_patch = patch.object(mod, 'SAFE_SOURCE_BLOB', mod._git_blob_id(self.safe_source))
        self.pin_patch.start()
        self.addCleanup(self.pin_patch.stop)
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.candidate = self.ws / mod.SAFE_SOURCE_PATH
        self.candidate.parent.mkdir(parents=True)
        self.candidate.write_bytes(self.safe_source)

    def runner(self, cmd, cwd=None):
        if cmd[:3] == ['gh','api','user']:
            return 0, json.dumps({'id':mod.AUTHORIZED_USER_ID,'login':'pddkalyan'}), '', 'SUCCESS'
        if cmd[:4] == ['git','remote','get-url','origin']:
            return 0, 'https://github.com/'+mod.REPO_NAME+'.git\n', '', 'SUCCESS'
        if cmd == ['git','rev-parse','--abbrev-ref','HEAD']:
            return 0, mod.EXPECTED_BRANCH+'\n', '', 'SUCCESS'
        if cmd == ['git','rev-parse','HEAD']:
            return 0, mod.EXPECTED_HEAD_SHA+'\n', '', 'SUCCESS'
        if cmd == ['git','status','--porcelain']:
            return 0, '?? report.md\n?? report_final.md\n', '', 'SUCCESS'
        raise AssertionError(cmd)

    def snapshot(self):
        return {str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}

    def run_boot(self, active=False, runner=None):
        return mod.execute_bootstrap(str(self.ws),activate_ping=active,defer_metadata_to_sidecar=True, runner=runner or self.runner)

    def test_preflight_is_strictly_read_only(self):
        before = self.snapshot()
        code, result = self.run_boot()
        self.assertEqual(code,0)
        self.assertEqual(result['status'],'READY_TO_INSTALL_READ_ONLY')
        self.assertEqual(before,self.snapshot())

    def test_activation_updates_only_live_script_and_private_config_and_backup(self):
        code, result = self.run_boot(active=True)
        self.assertEqual(code,0)
        self.assertEqual(result['status'],'PING_ONLY_INSTALLED_RELOAD_REQUIRED')
        self.assertEqual(self.source.read_bytes(),self.safe_source)
        cfg = json.loads(self.config.read_text())
        self.assertTrue(cfg['ping_enabled'])
        self.assertFalse(cfg['dispatch_enabled'])
        self.assertFalse(cfg['allow_dirty_continue'])
        self.assertEqual(cfg['conversation_id'],'private-target')
        self.assertTrue((self.state/'backups').is_dir())
        self.assertFalse((self.home/'.gemini'/'config'/'sidecars'/'antigravity_bridge').exists())
        self.assertEqual(list(self.ws.glob('report.md')),[])

    def test_rollback_failed_atomic_config_write(self):
        old_source = self.source.read_bytes()
        old_config = self.config.read_bytes()
        original_replace = mod._atomic_replace
        def fail_once(path, data):
            if path == self.config and data != old_config:
                raise OSError('DISK FULL')
            return original_replace(path, data)
        with patch.object(mod, '_atomic_replace',side_effect=fail_once):
            code, report = self.run_boot(active=True)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: INSTALLATION_ROLLED_BACK')
        self.assertEqual(self.source.read_bytes(),old_source)
        self.assertEqual(self.config.read_bytes(),old_config)

    def test_repo_lookalike_and_insecure_scheme_rejected(self):
        for origin in ['https://github.com.evil.com/'+mod.REPO_NAME, 'http://github.com/'+mod.REPO_NAME,
                       'https://github.com/'+mod.REPO_NAME+'.git.evil',
                       'https://github.com/'+mod.REPO_NAME+'@evil.com/repo']:
            def evil(cmd,cwd=None):
                if cmd[:2] == ['git','remote']:
                    return 0,origin,'','SUCCESS'
                return self.runner(cmd,cwd)
            code,report = self.run_boot(runner=evil)
            self.assertEqual(code,1,origin)
            self.assertEqual(report['status'],'BLOCKED: REPO_MISMATCH')

    def test_wrong_branch_and_head_sha_rejected(self):
        for cmdmatch,replacement,status in [
            (['git','rev-parse','--abbrev-ref','HEAD'],'main','BRANCH_MISMATCH'),
            (['git','rev-parse','HEAD'],'0'*40,'HEAD_SHA_MISMATCH')]:
            def incorrect(cmd,cwd=None):
                if cmd == cmdmatch: return 0,replacement,'','SUCCESS'
                return self.runner(cmd,cwd)
            code,report=self.run_boot(runner=incorrect)
            self.assertEqual(code,1)
            self.assertEqual(report['status'],'BLOCKED: '+status)

    def test_pin_blocks_changed_source(self):
        self.candidate.write_bytes(b'class AntigravityRemoteBridge:\n    print("tampered")\n')
        def unavailable(cmd,cwd=None):
            if cmd[:2] == ['gh','api'] and cmd[2] != 'user':
                return 1,'','rate limit','RATE_LIMITED'
            return self.runner(cmd,cwd)
        code,report=self.run_boot(runner=unavailable)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: PINNED_SOURCE_UNAVAILABLE')
        self.assertEqual(self.source.read_text(),'ORIGINAL STATUS-ONLY SIDECAR')

    def test_pin_rejects_bad_github_payload_even_if_sha_field_matches(self):
        self.candidate.unlink()
        def fake_api(cmd,cwd=None):
            if cmd[:2] == ['gh','api'] and cmd[2] != 'user':
                self.assertIn(mod.SAFE_SOURCE_COMMIT,cmd[2])
                return 0,json.dumps({'sha':mod.SAFE_SOURCE_BLOB,'encoding':'base64',
                           'content':base64.b64encode(b'malicious').decode()}),'','SUCCESS'
            return self.runner(cmd,cwd)
        code, report = self.run_boot(runner=fake_api)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: PINNED_SOURCE_MISMATCH')

    def test_pinned_github_api_no_raw_dependency(self):
        self.candidate.unlink()
        def fake_api(cmd,cwd=None):
            if cmd[:2] == ['gh','api'] and cmd[2] != 'user':
                return 0,json.dumps({'sha':mod.SAFE_SOURCE_BLOB,'encoding':'base64',
                           'content':base64.b64encode(self.safe_source).decode()}),'','SUCCESS'
            return self.runner(cmd,cwd)
        code, report = self.run_boot(runner=fake_api)
        self.assertEqual(code,0)
        self.assertEqual(report['status'],'READY_TO_INSTALL_READ_ONLY')

    def test_wrong_owner_and_transient_child_failures(self):
        attempts=[0]
        def intermittent(cmd,cwd=None):
            if cmd[:3] == ['gh','api','user']:
                attempts[0]+=1
                if attempts[0]<3:
                    return 1,'','temporary','CHILD_FAILED'
            return self.runner(cmd,cwd)
        with patch.object(mod.time,'sleep',return_value=None):
            code,report=self.run_boot(runner=intermittent)
        self.assertEqual(code,0)
        self.assertEqual(attempts[0],3)
        def wrong_owner(cmd,cwd=None):
            if cmd[:3] == ['gh','api','user']:
                return 0,json.dumps({'id':999,'login':'pddkalyan'}),'','SUCCESS'
            return self.runner(cmd,cwd)
        code,report=self.run_boot(runner=wrong_owner)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: GH_OWNER_MISMATCH')

    def test_corrupt_config_preserved_unmodified(self):
        self.config.write_text('{bad json')
        before=self.snapshot()
        code,report=self.run_boot(active=True)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: PRIVATE_CONFIG_INVALID')
        self.assertEqual(self.snapshot(),before)

    def test_unbound_id_and_dispatch_enabled_rejected(self):
        for changes,status in [({'conversation_id':''},'PRIVATE_CONVERSATION_UNBOUND'),
                               ({'dispatch_enabled':True},'BROAD_DISPATCH_MUST_BE_DISABLED')]:
            original=json.loads(self.config.read_text())
            self.config.write_text(json.dumps({**original,**changes}))
            code,report=self.run_boot(active=True)
            self.assertEqual(code,1)
            self.assertEqual(report['status'],'BLOCKED: '+status)
            self.config.write_text(json.dumps(original))

    def test_competing_sidecar_rejected(self):
        competitor=self.home/'.gemini'/'config'/'sidecars'/'antigravity_bridge'/'sidecar.json'
        competitor.parent.mkdir(parents=True)
        competitor.write_text('{}')
        code,report=self.run_boot(active=True)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: COMPETING_SIDECAR_PRESENT')

    def test_bogus_manifest_rejected_without_writes(self):
        manifest=self.sidecar/'sidecar.json'
        manifest.write_text(json.dumps({'args':['../../outside/bridge.py']}))
        before=self.snapshot()
        code,report=self.run_boot(active=True)
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: SIDECAR_TARGET_MISMATCH')
        self.assertEqual(self.snapshot(),before)

    def test_no_ghost_state_created_on_failure(self):
        (self.home/'.antigravity_bridge_state'/'config.json').unlink()
        before=self.snapshot()
        code,report=self.run_boot()
        self.assertEqual(code,1)
        self.assertEqual(report['status'],'BLOCKED: PRIVATE_CONFIG_MISSING')
        self.assertEqual(before,self.snapshot())

    def test_redaction(self):
        secret='ghp_'+'a'*32
        self.assertNotIn(secret,mod.sanitize_text('Bearer x '+secret))
        self.assertEqual(mod.sanitize_text(secret),'[REDACTED_SECRET]')


if __name__=='__main__': unittest.main()
