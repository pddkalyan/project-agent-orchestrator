/* Movie Studio Control Center — public GitHub evidence only, no execution control. */
'use strict';

const REPO = 'pddkalyan/project-agent-orchestrator';
const API = 'https://api.github.com/repos/' + REPO;
const EXPECTED_OWNER_ID = 159762630;
const BRIDGE_STALE_MS = 35 * 60 * 1000;

const WORKSTREAMS = [
    {
        id: 'ui-control-center',
        prNumber: 46,
        title: 'Control Center UI',
        owner: 'Antigravity CLI (Gemini Flash)',
        scope: 'Read-only UI, responsive layout, DOM safety & offline test harness',
        caveat: 'Read-only status viewer; no live agent execution or remote control'
    },
    {
        id: 'phase2-core',
        prNumber: 83,
        title: 'Phase 2 Production Pipeline',
        owner: 'GPT-6 / Sol Phase 2',
        scope: 'TASK-0005: Astra F1 cleanup restore + F2 atomic success callbacks',
        caveat: 'Genuine Astra review gate pending (Work limit reset); unmerged draft'
    },
    {
        id: 'bridge-security',
        prNumber: 79,
        title: 'Bridge Security Dispatch Boundaries',
        owner: 'Jules',
        scope: 'Native Windows CLI dispatch limits, token validation & sidecar containment',
        caveat: 'Upstream security boundary; Jules-owned, pending review'
    },
    {
        id: 'bridge-fifo',
        prNumber: 82,
        title: 'Inbound Command FIFO Queue',
        owner: 'Jules',
        scope: 'Draft inbound queue FIFO controller; dependent on PR #79 security fixes',
        caveat: 'Main-based draft; unsafe for deployment until security review complete'
    }
];

let cachedPRs = null;
let cachedRuns = null;
let cachedBridgeReceipt = null;

function githubURL(raw, kind) {
    if (typeof raw !== 'string') return null;
    try {
        const url = new URL(raw);
        if (url.protocol !== 'https:' || url.hostname !== 'github.com' ||
            url.username || url.password || url.search || url.hash || url.port) return null;
        const root = '/pddkalyan/project-agent-orchestrator/';
        if (!url.pathname.startsWith(root)) return null;
        const path = url.pathname.slice(root.length);
        const match = kind === 'pr'
            ? /^pull\/[0-9]+\/?$/.test(path)
            : /^actions\/runs\/[0-9]+\/?$/.test(path);
        return match ? url.href : null;
    } catch (_) {
        return null;
    }
}

function textElement(tag, content, className) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    el.textContent = content == null ? 'Unknown' : String(content);
    return el;
}

function linkOrText(label, rawURL, kind, className) {
    const url = githubURL(rawURL, kind);
    const el = textElement(url ? 'a' : 'span', label, className);
    if (url) {
        el.href = url;
        el.target = '_blank';
        el.rel = 'noopener noreferrer';
    }
    return el;
}

function clearWithMessage(container, message, className) {
    container.replaceChildren(textElement('div', message, className ||
        'text-sm text-cinematic-muted text-center mt-4'));
}

function validDate(value) {
    if (value == null || typeof value === 'boolean' || (typeof value === 'string' && !value.trim())) return null;
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date : null;
}

function dateLabel(value) {
    const date = validDate(value);
    return date ? date.toLocaleDateString() : 'Unknown date';
}

async function getPublicJSON(endpoint) {
    const response = await fetch(API + endpoint, {headers: {'Accept': 'application/vnd.github+json'}});
    if (!response.ok) {
        const error = new Error(response.status === 403 || response.status === 429 ?
            'GitHub rate limit or access restriction' : 'GitHub request failed');
        error.status = response.status;
        throw error;
    }
    return response.json();
}

function knownFailureMessage(err, what) {
    return (err && (err.status === 403 || err.status === 429)) ?
        'GitHub rate limit or access restriction. Try again later.' :
        'Unable to load ' + what + '. Connection or GitHub API unavailable.';
}

function addPR(container, pr) {
    if (!pr || typeof pr !== 'object') return;
    let status = 'OPEN';
    let statusClass = 'status-open';
    if (pr.merged_at) { status = 'MERGED'; statusClass = 'status-merged'; }
    else if (pr.state === 'closed') { status = 'CLOSED'; statusClass = 'status-closed'; }
    else if (pr.draft === true) { status = 'DRAFT'; statusClass = 'status-neutral'; }

    const wrapper = document.createElement('div');
    wrapper.className = 'border-b border-cinematic-border/50 py-3 last:border-0 px-2';
    const top = document.createElement('div');
    top.className = 'flex justify-between items-start mb-1 gap-3';
    const title = (typeof pr.title === 'string' && pr.title.trim()) ? pr.title.trim() : 'Untitled pull request';
    top.appendChild(linkOrText(title, pr.html_url, 'pr',
        'text-sm font-semibold text-white truncate'));
    top.appendChild(textElement('span', status, 'status-badge ' + statusClass));
    const author = (pr.user && typeof pr.user.login === 'string' && pr.user.login.trim()) ? pr.user.login.trim() : 'Unknown';
    const detail = textElement('div',
        '#' + (Number.isSafeInteger(pr.number) ? pr.number : '?') +
        ' by ' + author +
        ' · ' + dateLabel(pr.created_at),
        'text-xs text-cinematic-muted font-mono');
    wrapper.appendChild(top);
    wrapper.appendChild(detail);
    container.appendChild(wrapper);
}

async function fetchPRs() {
    const container = document.getElementById('pr-container');
    if (container) clearWithMessage(container, 'Loading pull requests…');
    try {
        const data = await getPublicJSON('/pulls?state=all&per_page=30');
        if (!Array.isArray(data)) throw new Error('Invalid PR data');
        cachedPRs = data;
        renderWorkboard();
        if (container) {
            container.replaceChildren();
            if (data.length === 0) return clearWithMessage(container, 'No pull requests found.');
            data.slice(0, 10).forEach(pr => addPR(container, pr));
        }
    } catch (err) {
        if (container) clearWithMessage(container, knownFailureMessage(err, 'pull requests'), 'text-sm text-red-400 text-center');
        renderWorkboard();
    }
}

function runStatus(run) {
    if (run.status !== 'completed') return ['RUNNING / QUEUED', 'status-in_progress'];
    switch (run.conclusion) {
        case 'success': return ['SUCCESS', 'status-success'];
        case 'failure': return ['FAILED', 'status-failure'];
        case 'cancelled': return ['CANCELLED', 'status-neutral'];
        case 'skipped': return ['SKIPPED', 'status-neutral'];
        default: return ['COMPLETED / UNKNOWN', 'status-neutral'];
    }
}

function addRun(container, run) {
    if (!run || typeof run !== 'object') return;
    const [state, color] = runStatus(run);
    const wrapper = document.createElement('div');
    wrapper.className = 'border-b border-cinematic-border/50 py-3 last:border-0 px-2';
    const top = document.createElement('div');
    top.className = 'flex justify-between items-start gap-3';
    const name = (typeof run.name === 'string' && run.name.trim()) ? run.name.trim() : 'Unnamed workflow';
    top.appendChild(linkOrText(name, run.html_url, 'run',
        'text-sm font-semibold text-white truncate'));
    top.appendChild(textElement('span', state, 'text-xs font-mono ' + color));
    wrapper.appendChild(top);

    let extraNote = '';
    if (name.includes('Trusted Jules Launcher')) {
        extraNote = ' · [Policy / Scheduled Launcher Check — not active agent coding]';
    }

    let displayTitle = (typeof run.display_title === 'string' && run.display_title.trim()) ? run.display_title.trim() : 'No title';
    if (displayTitle.includes('Jules is on it')) {
        displayTitle += ' (Task acknowledgment only — not ongoing thought)';
    }

    const headBranch = (typeof run.head_branch === 'string' && run.head_branch.trim()) ? run.head_branch.trim() : 'Unknown branch';
    wrapper.appendChild(textElement('div',
        displayTitle + ' · ' +
        headBranch + ' · ' + dateLabel(run.created_at) + extraNote,
        'text-xs text-cinematic-muted font-mono'));
    container.appendChild(wrapper);
}

async function fetchActions() {
    const container = document.getElementById('actions-container');
    if (container) clearWithMessage(container, 'Loading workflow runs…');
    try {
        const data = await getPublicJSON('/actions/runs?per_page=20');
        if (!data || !Array.isArray(data.workflow_runs)) throw new Error('Invalid actions data');
        cachedRuns = data.workflow_runs;
        renderWorkboard();
        if (container) {
            container.replaceChildren();
            if (data.workflow_runs.length === 0)
                return clearWithMessage(container, 'No workflow runs found.');
            data.workflow_runs.slice(0, 10).forEach(run => addRun(container, run));
        }
    } catch (err) {
        if (container) clearWithMessage(container, knownFailureMessage(err, 'workflow runs'), 'text-sm text-red-400 text-center');
        renderWorkboard();
    }
}

function renderWorkboard() {
    const container = document.getElementById('workboard-container');
    if (!container) return;
    const cards = WORKSTREAMS.map(stream => {
        const pr = Array.isArray(cachedPRs) ? cachedPRs.find(p => p && p.number === stream.prNumber) : null;
        let prStatus = 'UNKNOWN';
        let prStatusClass = 'status-neutral';
        if (pr) {
            if (pr.merged_at) { prStatus = 'MERGED'; prStatusClass = 'status-merged'; }
            else if (pr.state === 'closed') { prStatus = 'CLOSED'; prStatusClass = 'status-closed'; }
            else if (pr.draft === true) { prStatus = 'DRAFT'; prStatusClass = 'status-neutral'; }
            else if (pr.state === 'open') { prStatus = 'OPEN'; prStatusClass = 'status-open'; }
        }

        const headSha = (pr && pr.head && typeof pr.head.sha === 'string') ? pr.head.sha : null;
        const headRef = (pr && pr.head && typeof pr.head.ref === 'string') ? pr.head.ref : null;
        const updated = pr ? dateLabel(pr.updated_at || pr.created_at) : 'Unknown date';

        let ciLabel = 'UNKNOWN';
        let ciClass = 'text-yellow-400';
        let ciRunURL = null;
        let ciDetail = 'Awaiting public CI evidence';

        if (headSha && Array.isArray(cachedRuns)) {
            const matchingRun = cachedRuns.find(r => r && r.head_sha === headSha);
            if (matchingRun) {
                const [statusText, colorClass] = runStatus(matchingRun);
                ciLabel = statusText;
                ciClass = colorClass.replace('status-', 'text-');
                ciRunURL = matchingRun.html_url;
                if (matchingRun.name && matchingRun.name.includes('Trusted Jules Launcher')) {
                    ciDetail = 'Policy / Scheduled Launcher Check';
                } else {
                    ciDetail = (matchingRun.name || 'CI') + ' (' + headSha.slice(0, 7) + ')';
                }
            } else {
                ciLabel = 'NO RUN FOR HEAD';
                ciClass = 'text-cinematic-muted';
                ciDetail = 'No verified CI run for head ' + headSha.slice(0, 7);
            }
        } else if (!pr) {
            ciDetail = 'PR data unavailable';
        }

        let bridgeText = 'Cloud isolated';
        let bridgeClass = 'text-cinematic-muted';
        if (stream.id === 'ui-control-center') {
            if (cachedBridgeReceipt) {
                const age = Date.now() - cachedBridgeReceipt.observed.getTime();
                if (age >= 0 && age <= BRIDGE_STALE_MS) {
                    bridgeText = cachedBridgeReceipt.bridge_process_alive ? 'Alive (receipt verified)' : 'Reported stopped';
                    bridgeClass = cachedBridgeReceipt.bridge_process_alive ? 'text-green-400' : 'text-red-400';
                } else {
                    bridgeText = 'STALE (> 35m)';
                    bridgeClass = 'text-yellow-400';
                }
            } else {
                bridgeText = 'UNKNOWN (no receipt)';
                bridgeClass = 'text-yellow-400';
            }
        }

        const card = document.createElement('div');
        card.className = 'workboard-card p-3 rounded-lg flex flex-col gap-2.5';

        // Top Row: Title + Status Badge
        const topRow = document.createElement('div');
        topRow.className = 'flex justify-between items-start gap-2 flex-wrap';
        const titleBox = document.createElement('div');
        titleBox.className = 'min-w-0';
        const prTargetURL = pr ? pr.html_url : ('https://github.com/' + REPO + '/pull/' + stream.prNumber);
        titleBox.appendChild(linkOrText(stream.title + ' (#' + stream.prNumber + ')', prTargetURL, 'pr',
            'text-xs font-bold text-white hover:text-cinematic-accent2 transition-colors block truncate'));
        titleBox.appendChild(textElement('div', 'Owner: ' + stream.owner, 'text-[11px] text-cinematic-accent2 font-mono mt-0.5'));
        topRow.appendChild(titleBox);
        topRow.appendChild(textElement('span', prStatus, 'status-badge ' + prStatusClass));
        card.appendChild(topRow);

        // Scope
        card.appendChild(textElement('div', stream.scope, 'text-[11px] text-cinematic-muted leading-tight'));

        // Evidence Box
        const evidenceBox = document.createElement('div');
        evidenceBox.className = 'border-t border-cinematic-border/40 pt-2 flex flex-col gap-1.5 font-mono text-[11px]';

        // Published code line
        const codeRow = document.createElement('div');
        codeRow.className = 'flex justify-between items-start gap-1';
        codeRow.appendChild(textElement('span', 'Code / Branch:', 'text-cinematic-muted shrink-0'));
        const codeValue = headRef ? (headRef.slice(0, 16) + '… @ ' + headSha.slice(0, 7) + ' · ' + updated) : 'Unknown';
        codeRow.appendChild(textElement('span', codeValue, 'text-gray-300 text-right truncate text-[10px]'));
        evidenceBox.appendChild(codeRow);

        // CI Line
        const ciRow = document.createElement('div');
        ciRow.className = 'flex justify-between items-start gap-1';
        ciRow.appendChild(textElement('span', 'Workflow CI:', 'text-cinematic-muted shrink-0'));
        const ciBox = document.createElement('span');
        ciBox.className = 'text-right text-[10px] ' + ciClass;
        if (ciRunURL) {
            ciBox.appendChild(linkOrText(ciDetail + ' [' + ciLabel + ']', ciRunURL, 'run', 'underline hover:text-white'));
        } else {
            ciBox.textContent = ciDetail + ' [' + ciLabel + ']';
        }
        ciRow.appendChild(ciBox);
        evidenceBox.appendChild(ciRow);

        // Bridge Heartbeat Line
        const bridgeRow = document.createElement('div');
        bridgeRow.className = 'flex justify-between items-center gap-1';
        bridgeRow.appendChild(textElement('span', 'Bridge Heartbeat:', 'text-cinematic-muted shrink-0'));
        bridgeRow.appendChild(textElement('span', bridgeText, 'text-[10px] ' + bridgeClass));
        evidenceBox.appendChild(bridgeRow);

        // Native Agent Line
        const agentRow = document.createElement('div');
        agentRow.className = 'flex justify-between items-center gap-1';
        agentRow.appendChild(textElement('span', 'Native Agent:', 'text-cinematic-muted shrink-0'));
        agentRow.appendChild(textElement('span', 'UNKNOWN — no direct session telemetry', 'text-[10px] text-yellow-400 text-right'));
        evidenceBox.appendChild(agentRow);

        card.appendChild(evidenceBox);

        // Caveat
        card.appendChild(textElement('div', stream.caveat, 'mt-auto pt-2 border-t border-cinematic-border/30 text-[10px] text-cinematic-muted italic'));
        return card;
    });

    container.replaceChildren(...cards);
    const updatedTag = document.getElementById('workboard-updated');
    if (updatedTag) {
        updatedTag.textContent = 'Evidence updated: ' + new Date().toLocaleTimeString();
    }
}

function receiptFromComment(comment) {
    if (!comment || !comment.user || comment.user.id !== EXPECTED_OWNER_ID ||
        typeof comment.body !== 'string' ||
        !comment.body.startsWith('### Antigravity Bridge Status Receipt')) return null;
    const match = comment.body.match(/```json\s*([\s\S]*?)\s*```/);
    if (!match || match[1].length > 10000) return null;
    try {
        const receipt = JSON.parse(match[1]);
        const observed = validDate(receipt.observation_utc);
        if (!observed || !/^[0-9a-f]{40}$/i.test(receipt.git_head_sha) ||
            receipt.configured_branch !== 'agent/control-center-standalone' ||
            typeof receipt.bridge_process_alive !== 'boolean' ||
            typeof receipt.git_dirty !== 'boolean') return null;
        return {...receipt, observed};
    } catch (_) {
        return null;
    }
}

function renderBridgeStatus(receipt, currentMS) {
    const status = document.getElementById('bridge-status');
    const detail = document.getElementById('bridge-detail');
    const agentState = document.getElementById('agent-state');
    if (!status || !detail) return;
    const age = receipt ? currentMS - receipt.observed.getTime() : Number.POSITIVE_INFINITY;
    const isFresh = age >= 0 && age <= BRIDGE_STALE_MS;
    const label = !receipt ? 'UNKNOWN' : !isFresh ? 'STALE' :
        receipt.bridge_process_alive ? 'BRIDGE REPORTED ALIVE' : 'BRIDGE REPORTED STOPPED';
    status.textContent = label;
    status.className = !receipt || !isFresh ? 'text-yellow-400 font-mono' :
        receipt.bridge_process_alive ? 'text-green-400 font-mono' : 'text-red-400 font-mono';
    detail.textContent = receipt ?
        'Last GitHub receipt: ' + receipt.observed.toLocaleString() +
        ' · ' + receipt.git_head_sha.slice(0, 8) +
        ' · dirty tree: ' + String(receipt.git_dirty) : 'No verified bridge receipt available.';
    if (agentState) agentState.textContent = 'UNKNOWN (no native agent telemetry)';
}

async function fetchBridgeStatus() {
    renderBridgeStatus(null, Date.now());
    try {
        // Issue #55 grows by roughly 96 receipts per day. Restrict the query
        // to the recent window so the first GitHub page never silently shows
        // yesterday's heartbeat once the issue has over 100 comments.
        const since = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
        const data = await getPublicJSON(
            '/issues/55/comments?per_page=100&since=' + encodeURIComponent(since)
        );
        if (!Array.isArray(data)) throw new Error('Invalid status data');
        const receipts = data.map(receiptFromComment).filter(Boolean)
            .sort((a, b) => b.observed.getTime() - a.observed.getTime());
        cachedBridgeReceipt = receipts[0] || null;
        renderBridgeStatus(cachedBridgeReceipt, Date.now());
        renderWorkboard();
    } catch (_) {
        // A failed API request never authorizes an online status.
        cachedBridgeReceipt = null;
        renderBridgeStatus(null, Date.now());
        renderWorkboard();
    }
}

function startControlCenter() {
    renderWorkboard();
    fetchPRs();
    fetchActions();
    fetchBridgeStatus();
    const prRefresh = document.getElementById('refresh-prs');
    const runsRefresh = document.getElementById('refresh-actions');
    const bridgeRefresh = document.getElementById('refresh-bridge');
    const workboardRefresh = document.getElementById('refresh-workboard');
    if (prRefresh) prRefresh.addEventListener('click', fetchPRs);
    if (runsRefresh) runsRefresh.addEventListener('click', fetchActions);
    if (bridgeRefresh) bridgeRefresh.addEventListener('click', fetchBridgeStatus);
    if (workboardRefresh) workboardRefresh.addEventListener('click', () => {
        fetchPRs();
        fetchActions();
        fetchBridgeStatus();
    });
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', startControlCenter);
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {githubURL, validDate, runStatus, receiptFromComment,
        renderBridgeStatus, addPR, addRun, fetchPRs, fetchActions, fetchBridgeStatus,
        knownFailureMessage, renderWorkboard, WORKSTREAMS};
}
