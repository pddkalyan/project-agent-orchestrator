"""Offline regression coverage for the public Movie Studio UI.

Runtime tests use Node.js with an isolated minimal DOM double, without external
packages, credentials, network access or browser installation.
"""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "control_center" / "web"


class FrontendStaticTests(unittest.TestCase):
    def test_files_exist(self):
        for name in ("app.js", "index.html", "style.css"):
            self.assertTrue((WEB / name).is_file(), name)

    def test_read_only_controls_remain_disabled(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('disabled aria-label="Approve Next Step"', html)
        self.assertIn('disabled aria-label="Halt Execution"', html)
        self.assertIn('id="command-input" type="text" disabled', html)

    def test_unverified_native_and_quota_state_is_not_fabricated(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="bridge-status"', html)
        self.assertIn('id="bridge-detail"', html)
        self.assertIn('Native agent execution:', html)
        self.assertIn('Live model:', html)
        self.assertIn('UNKNOWN', html)
        self.assertNotIn('System Online', html)
        self.assertNotIn('Quota enforces ₹0 limit.', html)
        self.assertNotIn('Execution Paused', html)

    def test_css_is_plain_css_not_unsupported_directive(self):
        css = (WEB / "style.css").read_text(encoding="utf-8")
        self.assertIn(".status-badge", css)
        self.assertIn(".status-neutral", css)
        self.assertNotIn("@apply", css)

    def test_no_untrusted_innerhtml_sink(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn(".innerHTML", js)
        self.assertNotIn("err.message", js)
        self.assertIn("textContent", js)
        self.assertIn("noopener noreferrer", js)


NODE_BEHAVIOR_TESTS = r"""
'use strict';
const assert = require('node:assert/strict');
const app = require(process.argv[1]);
const elements = new Map();
class FakeNode {
    constructor(tag) {
        this.tagName = tag.toLowerCase();
        this.textContent = '';
        this.children = [];
        this.className = '';
        this.href = null;
        this.rel = null;
        this.target = null;
        this.events = {};
    }
    appendChild(item) { this.children.push(item); return item; }
    replaceChildren(...items) { this.children = items; }
    addEventListener(type, handler) { this.events[type] = handler; }
}
global.document = {
    createElement: tag => new FakeNode(tag),
    getElementById: id => elements.get(id) || null,
    addEventListener: () => {}
};
function node(id) { const n = new FakeNode('div'); elements.set(id, n); return n; }
function gather(root) {
    return [root, ...root.children.flatMap(gather)];
}
function assertBadURL(value) {
    assert.equal(app.githubURL(value, 'pr'), null, 'unsafe URL accepted: ' + value);
}
for (const url of [
    'javascript:alert(1)',
    'https://github.com.attacker.example/pddkalyan/project-agent-orchestrator/pull/10',
    'http://github.com/pddkalyan/project-agent-orchestrator/pull/10',
    'https://github.com@evil.example/pddkalyan/project-agent-orchestrator/pull/10',
    'https://github.com/pddkalyan/project-agent-orchestrator/actions/runs/10',
    'https://github.com/pddkalyan/project-agent-orchestrator/pull/1?redirect=1',
    null,
]) assertBadURL(url);
assert.equal(app.githubURL('https://github.com/pddkalyan/project-agent-orchestrator/pull/71', 'pr'),
    'https://github.com/pddkalyan/project-agent-orchestrator/pull/71');
assert.equal(app.githubURL('https://github.com/pddkalyan/project-agent-orchestrator/actions/runs/42', 'run'),
    'https://github.com/pddkalyan/project-agent-orchestrator/actions/runs/42');
const prs = node('pr-container');
app.addPR(prs, {
    title: '<img src=x onerror=alert(1)>',
    html_url: 'javascript:alert(1)',
    number: 20,
    draft: true,
    user: {login: '"><svg onload=alert(1)>'},
    created_at: 'not-a-date'
});
assert.equal(prs.children.length, 1);
let all = gather(prs);
assert(!all.some(n => n.tagName === 'a'), 'unsafe URL created anchor');
assert(all.some(n => n.textContent.includes('<img src=x onerror=')),
    'external title was not treated as literal text');
assert(all.some(n => n.textContent.includes('Unknown date')));
app.addPR(prs, {
    title: 'Valid link',
    html_url: 'https://github.com/pddkalyan/project-agent-orchestrator/pull/46',
    number: 46,
    state: 'open'
});
const anchor = gather(prs).find(n => n.tagName === 'a');
assert(anchor, 'valid GitHub link missing');
assert.equal(anchor.rel, 'noopener noreferrer');
assert.equal(anchor.target, '_blank');
const actions = node('actions-container');
app.addRun(actions, {
    name: '<script>alert(1)</script>', display_title: 'dangerous <b>title</b>',
    html_url: 'data:text/html,<svg/onload=alert(1)>',
    status: 'completed', conclusion: 'cancelled'
});
assert(!gather(actions).some(n => n.tagName === 'a'));
assert(gather(actions).some(n => n.textContent.includes('CANCELLED')));
assert.equal(app.runStatus({status: 'queued'})[0], 'RUNNING / QUEUED');
assert.equal(app.runStatus({status: 'completed', conclusion: 'skipped'})[0], 'SKIPPED');
assert.equal(app.runStatus({status: 'completed', conclusion: null})[0], 'COMPLETED / UNKNOWN');
const fence = String.fromCharCode(96).repeat(3);
const rawReceipt = {
    receipt_id: 'test',
    observation_utc: '2026-10-10T04:44:28Z',
    configured_branch: 'agent/control-center-standalone',
    git_head_sha: '0e81f5b30f3e344592d1e9bf371ad0faa7c2ea19',
    git_dirty: true,
    bridge_process_alive: true
};
const comment = {
    user: {id: 159762630},
    body: '### Antigravity Bridge Status Receipt\n' +
        fence + 'json\n' + JSON.stringify(rawReceipt) + '\n' + fence
};
assert(app.receiptFromComment(comment));
assert.equal(app.receiptFromComment({...comment, user: {id: 22}}), null);
assert.equal(app.receiptFromComment({...comment, body: 'not a receipt'}), null);
const bridge = node('bridge-status');
const bridgeDetail = node('bridge-detail');
const native = node('agent-state');
const receipt = app.receiptFromComment(comment);
app.renderBridgeStatus(receipt, receipt.observed.getTime() + 1000);
assert.equal(bridge.textContent, 'BRIDGE REPORTED ALIVE');
assert(native.textContent.includes('UNKNOWN'));
assert(bridgeDetail.textContent.includes('0e81f5b3'));
app.renderBridgeStatus(receipt, receipt.observed.getTime() + 36 * 60000);
assert.equal(bridge.textContent, 'STALE');
app.renderBridgeStatus(null, Date.now());
assert.equal(bridge.textContent, 'UNKNOWN');
global.fetch = async () => ({ok: false, status: 429});
(async () => {
    await app.fetchPRs();
    assert(prs.children[0].textContent.includes('rate limit'));
    assert(!prs.children[0].textContent.includes('undefined'));
    await app.fetchActions();
    assert(actions.children[0].textContent.includes('rate limit'));
    console.log('Behavior tests passed: URL injection, DOM escaping, workflow state, heartbeat, stale status, and 429.');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


class FrontendBehaviorTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js needed for JS behavior tests")
    def test_node_runtime_dom_and_security(self):
        process = subprocess.run(
            ["node", "-e", NODE_BEHAVIOR_TESTS, str(WEB / "app.js")],
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(process.returncode, 0,
                         "Node behavior tests failed:\n" + process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
