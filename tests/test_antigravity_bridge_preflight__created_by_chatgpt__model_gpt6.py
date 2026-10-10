import json
import tempfile
from pathlib import Path
import unittest

from control_center.remote_bridge import antigravity_bridge_preflight__created_by_chatgpt__model_gpt6 as preflight


class BridgePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / 'workspace'
        self.repo.mkdir()
        sidecar = self.home / '.gemini/config/sidecars/github_bridge'
        sidecar.mkdir(parents=True)
        (sidecar / 'bridge.py').write_text('pass\n')
        (sidecar / 'sidecar.json').write_text(json.dumps({'command': 'python'}))
        state = self.home / '.antigravity_bridge_state'
        state.mkdir()
        (state / 'config.json').write_text(json.dumps({
            'status_enabled': True, 'dispatch_enabled': False,
            'conversation_id': 'private-id', 'allow_dirty_continue': False}))
        (self.home / '.gemini/config/config.json').write_text(
            json.dumps({'sidecars': {'github_bridge': {'enabled': True}}}))

    def runner(self, argv, timeout=8):
        if argv[:2] == ['gh', 'auth']:
            return 0, ''
        if argv[-3:] == ['remote', 'get-url', 'origin']:
            return 0, preflight.ALLOWED_ORIGINS[0]
        if argv[-2:] == ['branch', '--show-current']:
            return 0, preflight.EXPECTED_BRANCH
        if argv[-2:] == ['rev-parse', 'HEAD']:
            return 0, preflight.EXPECTED_SHA
        if argv[-2:] == ['status', '--porcelain']:
            return 0, ' M local-file'
        return None, ''

    def inspect(self, run=None):
        return preflight.inspect(self.repo, self.home, run=run or self.runner)

    def by_name(self, checks, name):
        return next(x for x in checks if x['check'] == name)

    def test_healthy_status_only_stays_unverified_for_native_execution(self):
        result = self.inspect()
        self.assertFalse(result['safe_for_native_activation'])
        self.assertFalse(any(x['result'] == 'BLOCKED' for x in result['checks']))
        self.assertEqual(self.by_name(result['checks'], 'working_tree')['result'], 'DIRTY')
        self.assertEqual(self.by_name(result['checks'], 'native_agent_delivery')['result'], 'NOT_TESTED')

    def test_mismatched_head_fails_closed(self):
        def runner(argv, timeout=8):
            if argv[-2:] == ['rev-parse', 'HEAD']:
                return 0, 'f' * 40
            return self.runner(argv, timeout)
        self.assertEqual(self.by_name(self.inspect(runner)['checks'], 'expected_git_head')['result'], 'BLOCKED')

    def test_forged_github_origin_fails_closed(self):
        def runner(argv, timeout=8):
            if argv[-3:] == ['remote', 'get-url', 'origin']:
                return 0, 'https://github.com/pddkalyan/project-agent-orchestrator.evil'
            return self.runner(argv, timeout)
        self.assertEqual(self.by_name(self.inspect(runner)['checks'], 'exact_github_origin')['result'], 'BLOCKED')

    def test_dispatch_opt_in_is_reported_blocked(self):
        state = self.home / '.antigravity_bridge_state/config.json'
        config = json.loads(state.read_text())
        config['dispatch_enabled'] = True
        state.write_text(json.dumps(config))
        self.assertEqual(self.by_name(self.inspect()['checks'], 'no_general_dispatch')['result'], 'BLOCKED')

    def test_malformed_state_fails_closed_without_leaking_content(self):
        (self.home / '.antigravity_bridge_state/config.json').write_text('{bad:secret')
        result = self.inspect()
        self.assertEqual(self.by_name(result['checks'], 'local_opt_in_config')['result'], 'BLOCKED')
        self.assertNotIn('secret', json.dumps(result))

    def test_unverified_ping_does_not_get_marked_ready(self):
        state = self.home / '.antigravity_bridge_state/config.json'
        config = json.loads(state.read_text())
        config['ping_enabled'] = True
        state.write_text(json.dumps(config))
        self.assertEqual(self.by_name(self.inspect()['checks'], 'no_unverified_ping')['result'], 'BLOCKED')

    def test_missing_host_workspace_is_blocked(self):
        result = preflight.inspect(None, self.home, run=self.runner)
        self.assertEqual(self.by_name(result['checks'], 'workspace_supplied')['result'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
