#!/usr/bin/env python3
"""Read-only Antigravity GitHub bridge preflight. No activation, installation or prompts.

Designed for Windows host, Python 3.9+, zero external Python dependencies.
Never prints paths, credentials, conversation IDs or raw CLI output.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = 'pddkalyan/project-agent-orchestrator'
EXPECTED_BRANCH = 'agent/control-center-standalone'
EXPECTED_SHA = '15b148cfbc3acc0eec10c51ab8e8bf413613f23f'
ALLOWED_ORIGINS = (
    'https://github.com/' + REPO + '.git',
    'https://github.com/' + REPO,
    'git@github.com:' + REPO + '.git',
    'git@github.com:' + REPO,
)


def command(argv, timeout=8):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                shell=False, check=False)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return None


def inspect(workspace, home, expected_sha=EXPECTED_SHA, run=command):
    checks = []

    def check(name, passed, details):
        checks.append({'check': name, 'result': 'PASS' if passed else 'BLOCKED',
                       'detail': details})

    repo = Path(workspace).expanduser() if workspace else None
    check('workspace_supplied', bool(repo) and repo.is_dir(),
          'Provide --workspace pointing to the existing local repository')
    if repo and repo.is_dir():
        def git(*args):
            return run(['git', '-C', str(repo), *args])
        rc, origin = git('remote', 'get-url', 'origin')
        check('exact_github_origin', rc == 0 and origin in ALLOWED_ORIGINS,
              'Origin must match the designated GitHub repository exactly')
        rc, branch = git('branch', '--show-current')
        check('expected_branch', rc == 0 and branch == EXPECTED_BRANCH,
              'Expected isolated Control Center branch')
        rc, sha = git('rev-parse', 'HEAD')
        check('expected_git_head', rc == 0 and sha.lower() == expected_sha.lower(),
              'HEAD must match the exact reviewed Control Center commit')
        rc, dirty = git('status', '--porcelain')
        check('git_status_readable', rc == 0, 'Git status must be accessible')
        # Dirty tree can be normal for status-only; never reset it.
        checks.append({'check': 'working_tree', 'result': 'DIRTY' if dirty else 'CLEAN',
                       'detail': 'Not modified by this utility'})

    root = Path(home).expanduser()
    sidecar = root / '.gemini' / 'config' / 'sidecars' / 'github_bridge'
    state = root / '.antigravity_bridge_state' / 'config.json'
    manifest = read_json(sidecar / 'sidecar.json')
    config = read_json(state)
    global_config = read_json(root / '.gemini' / 'config' / 'config.json')

    check('installed_sidecar_files', (sidecar / 'bridge.py').is_file() and
          isinstance(manifest, dict),
          'Existing sidecar script and manifest present; no files copied')
    if isinstance(manifest, dict):
        check('sidecar_command_declared', isinstance(manifest.get('command'), str)
              and bool(manifest.get('command')),
              'Review official Antigravity sidecar command and args')
    check('local_opt_in_config', isinstance(config, dict),
          'Local bridge config must exist and parse as JSON')
    if isinstance(config, dict):
        check('status_opt_in', config.get('status_enabled') is True,
              'Status-only monitoring requires status_enabled=true')
        check('no_general_dispatch', config.get('dispatch_enabled') is False,
              'General native-agent dispatch must remain disabled')
        check('no_unverified_ping', config.get('ping_enabled', False) is False,
              'Ping disabled until separately reviewed source and native validation')
        check('no_dirty_continue', config.get('allow_dirty_continue', False) is False,
              'Dirty-worktree auto-continue must remain disabled')
        check('private_conversation_configured',
              isinstance(config.get('conversation_id'), str)
              and bool(config.get('conversation_id', '').strip()),
              'A private conversation ID must be configured locally; value never printed')
    enabled = (isinstance(global_config, dict)
               and isinstance(global_config.get('sidecars'), dict)
               and isinstance(global_config['sidecars'].get('github_bridge'), dict)
               and global_config['sidecars']['github_bridge'].get('enabled') is True)
    check('official_sidecar_enabled', enabled,
          'Antigravity global sidecars.github_bridge.enabled must be true')

    python_ok = sys.version_info >= (3, 9)
    check('python_supported', python_ok, 'Python 3.9 or newer is required')
    gh_rc, _ = run(['gh', 'auth', 'status'])
    check('github_cli_authorized', gh_rc == 0,
          'Local GitHub CLI must be authenticated; no tokens are printed')
    owner_rc, owner_id = run(['gh', 'api', 'user', '--jq', '.id'])
    check('exact_github_owner', owner_rc == 0 and owner_id == '159762630',
          'CLI must authenticate as repository owner (identity redacted)')
    # Official published sidecars docs list send-message but not get-conversation-metadata.
    # Do NOT weaken the exact-target security check or send a probe based on this report.
    checks.append({'check': 'native_conversation_identity', 'result': 'UNVERIFIED',
                   'detail': 'Must be established by a separately reviewed local method before ping'})
    checks.append({'check': 'native_agent_delivery', 'result': 'NOT_TESTED',
                   'detail': 'No agentapi command was invoked'})
    return {'schema_version': 1, 'mode': 'READ_ONLY',
            'safe_for_native_activation': False, 'checks': checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workspace', help='Existing Control Center local repository directory')
    ap.add_argument('--expected-sha', default=EXPECTED_SHA,
                    help='Exact head reviewed for the local Control Center branch')
    args = ap.parse_args(argv)
    if not re.fullmatch(r'[0-9a-fA-F]{40}', args.expected_sha):
        ap.error('--expected-sha must be a 40-hex commit SHA')
    home = os.environ.get('USERPROFILE') or str(Path.home())
    result = inspect(args.workspace, home, args.expected_sha)
    print(json.dumps(result, indent=2))
    return 0 if all(x['result'] not in ('BLOCKED',) for x in result['checks']) else 2


if __name__ == '__main__':
    sys.exit(main())
