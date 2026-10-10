/* Movie Studio Control Center — public GitHub evidence only, no execution control. */
'use strict';

const REPO = 'pddkalyan/project-agent-orchestrator';
const API = 'https://api.github.com/repos/' + REPO;
const EXPECTED_OWNER_ID = 159762630;
const BRIDGE_STALE_MS = 35 * 60 * 1000;

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
    top.appendChild(linkOrText(pr.title || 'Untitled pull request', pr.html_url, 'pr',
        'text-sm font-semibold text-white truncate'));
    top.appendChild(textElement('span', status, 'status-badge ' + statusClass));
    const detail = textElement('div',
        '#' + (Number.isSafeInteger(pr.number) ? pr.number : '?') +
        ' by ' + (pr.user && pr.user.login || 'Unknown') +
        ' · ' + dateLabel(pr.created_at),
        'text-xs text-cinematic-muted font-mono');
    wrapper.appendChild(top);
    wrapper.appendChild(detail);
    container.appendChild(wrapper);
}

async function fetchPRs() {
    const container = document.getElementById('pr-container');
    if (!container) return;
    clearWithMessage(container, 'Loading pull requests…');
    try {
        const data = await getPublicJSON('/pulls?state=all&per_page=10');
        if (!Array.isArray(data)) throw new Error('Invalid PR data');
        container.replaceChildren();
        if (data.length === 0) return clearWithMessage(container, 'No pull requests found.');
        data.forEach(pr => addPR(container, pr));
    } catch (err) {
        clearWithMessage(container, knownFailureMessage(err, 'pull requests'), 'text-sm text-red-400 text-center');
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
    top.appendChild(linkOrText(run.name || 'Unnamed workflow', run.html_url, 'run',
        'text-sm font-semibold text-white truncate'));
    top.appendChild(textElement('span', state, 'text-xs font-mono ' + color));
    wrapper.appendChild(top);
    wrapper.appendChild(textElement('div',
        (run.display_title || 'No title') + ' · ' +
        (run.head_branch || 'Unknown branch') + ' · ' + dateLabel(run.created_at),
        'text-xs text-cinematic-muted font-mono'));
    container.appendChild(wrapper);
}

async function fetchActions() {
    const container = document.getElementById('actions-container');
    if (!container) return;
    clearWithMessage(container, 'Loading workflow runs…');
    try {
        const data = await getPublicJSON('/actions/runs?per_page=10');
        if (!data || !Array.isArray(data.workflow_runs)) throw new Error('Invalid actions data');
        container.replaceChildren();
        if (data.workflow_runs.length === 0)
            return clearWithMessage(container, 'No workflow runs found.');
        data.workflow_runs.forEach(run => addRun(container, run));
    } catch (err) {
        clearWithMessage(container, knownFailureMessage(err, 'workflow runs'), 'text-sm text-red-400 text-center');
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
        renderBridgeStatus(receipts[0] || null, Date.now());
    } catch (_) {
        // A failed API request never authorizes an online status.
        renderBridgeStatus(null, Date.now());
    }
}

function startControlCenter() {
    fetchPRs();
    fetchActions();
    fetchBridgeStatus();
    const prRefresh = document.getElementById('refresh-prs');
    const runsRefresh = document.getElementById('refresh-actions');
    const bridgeRefresh = document.getElementById('refresh-bridge');
    if (prRefresh) prRefresh.addEventListener('click', fetchPRs);
    if (runsRefresh) runsRefresh.addEventListener('click', fetchActions);
    if (bridgeRefresh) bridgeRefresh.addEventListener('click', fetchBridgeStatus);
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', startControlCenter);
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {githubURL, validDate, runStatus, receiptFromComment,
        renderBridgeStatus, addPR, addRun, fetchPRs, fetchActions, fetchBridgeStatus,
        knownFailureMessage};
}
