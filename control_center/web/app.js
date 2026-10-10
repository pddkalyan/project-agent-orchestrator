const REPO = 'pddkalyan/project-agent-orchestrator';

function escapeHTML(str) {
    if (!str) return '';
    return str.toString().replace(/[&<>'"]/g,
        tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag)
    );
}

async function fetchPRs() {
    const container = document.getElementById('pr-container');
    container.innerHTML = '<div class="flex items-center justify-center h-full text-cinematic-muted"><i class="fa-solid fa-circle-notch fa-spin mr-2"></i> Loading PRs...</div>';

    try {
        const response = await fetch(`https://api.github.com/repos/${REPO}/pulls?state=all&per_page=10`);
        if (!response.ok) throw new Error('Failed to fetch PRs');
        const prs = await response.json();

        container.innerHTML = '';
        if (prs.length === 0) {
            container.innerHTML = '<div class="text-sm text-cinematic-muted italic text-center mt-4">No PRs found.</div>';
            return;
        }

        prs.forEach(pr => {
            let statusClass = 'bg-green-900/40 text-green-400 border border-green-800';
            let statusText = 'OPEN';
            if (pr.merged_at) {
                statusClass = 'bg-purple-900/40 text-purple-400 border border-purple-800';
                statusText = 'MERGED';
            } else if (pr.state === 'closed') {
                statusClass = 'bg-red-900/40 text-red-400 border border-red-800';
                statusText = 'CLOSED';
            }

            const el = document.createElement('div');
            el.className = 'border-b border-cinematic-border/50 py-3 last:border-0 hover:bg-black/20 transition-colors px-2 -mx-2 rounded';
            el.innerHTML = `
                <div class="flex justify-between items-start mb-1">
                    <a href="${escapeHTML(pr.html_url)}" target="_blank" class="text-sm font-semibold text-white hover:text-cinematic-accent2 truncate pr-4">${escapeHTML(pr.title)}</a>
                    <span class="px-2 py-0.5 rounded text-xs font-bold uppercase tracking-wider ${statusClass} flex-shrink-0">${statusText}</span>
                </div>
                <div class="flex justify-between items-center text-xs text-cinematic-muted font-mono">
                    <span>#${pr.number} by ${escapeHTML(pr.user.login)}</span>
                    <span>${new Date(pr.created_at).toLocaleDateString()}</span>
                </div>
            `;
            container.appendChild(el);
        });
    } catch (err) {
        container.innerHTML = `<div class="text-red-400 text-sm text-center mt-4"><i class="fa-solid fa-triangle-exclamation"></i> Error loading PRs: ${err.message}</div>`;
    }
}

async function fetchActions() {
    const container = document.getElementById('actions-container');
    container.innerHTML = '<div class="flex items-center justify-center h-full text-cinematic-muted"><i class="fa-solid fa-circle-notch fa-spin mr-2"></i> Loading Actions...</div>';

    try {
        const response = await fetch(`https://api.github.com/repos/${REPO}/actions/runs?per_page=10`);
        if (!response.ok) throw new Error('Failed to fetch Actions');
        const data = await response.json();
        const runs = data.workflow_runs;

        container.innerHTML = '';
        if (!runs || runs.length === 0) {
            container.innerHTML = '<div class="text-sm text-cinematic-muted italic text-center mt-4">No recent runs found.</div>';
            return;
        }

        runs.forEach(run => {
            let icon = '<i class="fa-solid fa-circle-notch fa-spin text-yellow-400"></i>';
            if (run.status === 'completed') {
                if (run.conclusion === 'success') {
                    icon = '<i class="fa-solid fa-check-circle text-green-400"></i>';
                } else {
                    icon = '<i class="fa-solid fa-times-circle text-red-400"></i>';
                }
            }

            const el = document.createElement('div');
            el.className = 'border-b border-cinematic-border/50 py-3 last:border-0 hover:bg-black/20 transition-colors px-2 -mx-2 rounded flex gap-3 items-center';
            el.innerHTML = `
                <div class="text-lg">${icon}</div>
                <div class="flex-1 min-w-0">
                    <div class="text-sm text-gray-200 truncate font-semibold"><a href="${escapeHTML(run.html_url)}" target="_blank" class="hover:text-cinematic-accent2">${escapeHTML(run.name)}</a></div>
                    <div class="text-xs text-cinematic-muted font-mono truncate">
                        ${escapeHTML(run.display_title)} &bull; ${escapeHTML(run.head_branch)}
                    </div>
                </div>
                <div class="text-xs text-cinematic-muted text-right whitespace-nowrap">
                    ${new Date(run.created_at).toLocaleDateString()}
                </div>
            `;
            container.appendChild(el);
        });
    } catch (err) {
        container.innerHTML = `<div class="text-red-400 text-sm text-center mt-4"><i class="fa-solid fa-triangle-exclamation"></i> Error loading Actions: ${err.message}</div>`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    fetchPRs();
    fetchActions();

    document.getElementById('refresh-prs').addEventListener('click', fetchPRs);
    document.getElementById('refresh-actions').addEventListener('click', fetchActions);
});
