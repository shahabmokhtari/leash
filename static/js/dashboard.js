/* ==========================================================================
   Dashboard Page Logic
   ========================================================================== */

async function refreshData() {
    await Promise.all([loadStats(), loadTrends(), loadInsights(), loadAdaptiveStats(), loadHooksStatus(), loadLatencyStats(), loadScoreDistribution()]);
}

// ---- Stats ----
async function loadStats() {
    const cards = document.querySelectorAll('.stat-card');
    cards.forEach(c => c.classList.add('loading'));

    try {
        const data = await fetchApi('/api/dashboard/stats');

        document.getElementById('autoApprovedCount').textContent = data.autoApprovedToday;
        document.getElementById('deniedCount').textContent = data.deniedToday;
        document.getElementById('activeSessionCount').textContent = data.activeSessions;
        document.getElementById('avgSafetyScore').textContent = data.avgSafetyScore;

        cards.forEach(c => c.classList.remove('loading'));
    } catch (error) {
        cards.forEach(c => c.classList.remove('loading'));
        showErrorState('statsError', 'Failed to load statistics', error.message);
    }
}

// ---- Latency Stats ----
async function loadLatencyStats() {
    const container = document.getElementById('latencyContent');
    if (!container) return;

    try {
        const data = await fetchApi('/api/dashboard/latency');
        const o = data.overall || {};

        if (!o.count) {
            container.innerHTML = '<p style="color:var(--text-muted);">No latency data yet.</p>';
            return;
        }

        let html = '';

        // Overall stats row
        html += '<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:12px;">';
        html += _latencyCard('Requests', o.count, '');
        html += _latencyCard('Avg', o.avg, 'ms');
        html += _latencyCard('Median', o.median, 'ms');
        html += _latencyCard('P95', o.p95, 'ms');
        html += _latencyCard('Min', o.min, 'ms');
        html += _latencyCard('Max', o.max, 'ms');
        html += '</div>';

        // Per-provider breakdown
        const providers = data.byProvider || {};
        const providerKeys = Object.keys(providers);
        if (providerKeys.length > 0) {
            html += '<div style="margin-bottom:8px;font-weight:600;font-size:0.9em;">By AI CLI</div>';
            html += '<table style="width:100%;border-collapse:collapse;font-size:0.85em;font-family:var(--font-mono);">';
            html += '<thead><tr style="color:var(--text-muted);text-align:left;border-bottom:1px solid var(--border-color);">' +
                '<th style="padding:4px 8px;">AI CLI</th><th>Count</th><th>Avg</th><th>Median</th><th>P95</th><th>Min</th><th>Max</th></tr></thead><tbody>';
            for (const p of providerKeys) {
                const s = providers[p];
                html += `<tr style="border-bottom:1px solid var(--border-color);">` +
                    `<td style="padding:4px 8px;font-weight:600;">${escapeHtml(p)}</td>` +
                    `<td>${s.count}</td><td>${s.avg}ms</td><td>${s.median}ms</td><td>${s.p95}ms</td><td>${s.min}ms</td><td>${s.max}ms</td></tr>`;
            }
            html += '</tbody></table>';

            // Latency line chart over time by AI CLI
            html += '<div style="margin-top:16px;">' +
                '<div style="margin-bottom:8px;font-weight:600;font-size:0.9em;">Latency Over Time</div>' +
                '<canvas id="latencyLineChart" width="800" height="200" style="width:100%;height:200px;"></canvas>' +
                '<div id="latencyChartLegend" style="display:flex;gap:12px;justify-content:center;margin-top:6px;font-size:0.75em;color:var(--text-muted);flex-wrap:wrap;"></div>' +
                '</div>';
        }

        // Per-session breakdown (top sessions)
        const sessions = data.bySessions || [];
        if (sessions.length > 0) {
            html += '<div style="margin:12px 0 8px;font-weight:600;font-size:0.9em;">By Session (top ' + sessions.length + ')</div>';
            html += '<table style="width:100%;border-collapse:collapse;font-size:0.85em;font-family:var(--font-mono);">';
            html += '<thead><tr style="color:var(--text-muted);text-align:left;border-bottom:1px solid var(--border-color);">' +
                '<th style="padding:4px 8px;">Session</th><th>AI CLI</th><th>Count</th><th>Avg</th><th>Median</th><th>P95</th><th>Max</th></tr></thead><tbody>';
            for (const s of sessions) {
                const sid = (s.sessionId || '').substring(0, 12) + '...';
                html += `<tr style="border-bottom:1px solid var(--border-color);">` +
                    `<td style="padding:4px 8px;" title="${escapeHtml(s.sessionId)}">${escapeHtml(sid)}</td>` +
                    `<td>${escapeHtml(s.provider || '')}</td>` +
                    `<td>${s.count}</td><td>${s.avg}ms</td><td>${s.median}ms</td><td>${s.p95}ms</td><td>${s.max}ms</td></tr>`;
            }
            html += '</tbody></table>';
        }

        container.innerHTML = html;

        // Render line chart if time series data is available
        var timeSeries = data.timeSeries || [];
        if (timeSeries.length > 0) {
            _renderLatencyLineChart(timeSeries, providerKeys);
        }
    } catch {
        container.innerHTML = '<p style="color:var(--text-muted);">Failed to load latency data.</p>';
    }
}

function _latencyCard(label, value, unit) {
    return `<div style="text-align:center;padding:8px;border-radius:6px;background:var(--bg-secondary);border:1px solid var(--border-color);">
        <div style="font-size:1.3em;font-weight:700;color:var(--text-primary);">${value}${unit}</div>
        <div style="font-size:0.75em;color:var(--text-muted);margin-top:2px;">${label}</div>
    </div>`;
}

var _LATENCY_COLORS = ['#2563eb', '#d97706', '#7c3aed', '#16a34a', '#dc2626', '#0891b2'];
var _METRIC_STYLES = { avg: [1.0, [5, 0]], median: [0.7, [6, 4]], p95: [0.5, [2, 3]], max: [0.4, [1, 3]] };
var _latencyChartHidden = {}; // "provider|metric" -> true

function _renderLatencyLineChart(timeSeries, providerKeys) {
    var canvas = document.getElementById('latencyLineChart');
    var legendEl = document.getElementById('latencyChartLegend');
    if (!canvas || !legendEl) return;

    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    var W = rect.width, H = rect.height;
    var pad = { top: 10, right: 12, bottom: 30, left: 55 };
    var cW = W - pad.left - pad.right;
    var cH = H - pad.top - pad.bottom;

    // Group data: { "provider|metric": [{time, value}] }
    var allTimes = [];
    var series = {};
    var metrics = ['avg', 'median', 'p95', 'max'];

    for (var i = 0; i < timeSeries.length; i++) {
        var pt = timeSeries[i];
        if (allTimes.indexOf(pt.time) === -1) allTimes.push(pt.time);
        for (var m = 0; m < metrics.length; m++) {
            var key = pt.provider + '|' + metrics[m];
            if (!series[key]) series[key] = {};
            series[key][pt.time] = pt[metrics[m]];
        }
    }
    allTimes.sort();
    if (allTimes.length === 0) return;

    // Find max value across visible series
    var maxVal = 0;
    var seriesKeys = Object.keys(series);
    for (var k = 0; k < seriesKeys.length; k++) {
        if (_latencyChartHidden[seriesKeys[k]]) continue;
        var vals = Object.values(series[seriesKeys[k]]);
        for (var v = 0; v < vals.length; v++) {
            if (vals[v] > maxVal) maxVal = vals[v];
        }
    }
    if (maxVal === 0) maxVal = 1;
    maxVal = maxVal * 1.1; // 10% headroom

    // Clear
    ctx.clearRect(0, 0, W, H);

    // Axes
    var textColor = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#888';
    var gridColor = getComputedStyle(document.documentElement).getPropertyValue('--border-color').trim() || '#333';

    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 0.5;
    // Y grid lines (5 ticks)
    for (var gi = 0; gi <= 4; gi++) {
        var gy = pad.top + cH - (gi / 4) * cH;
        ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(W - pad.right, gy); ctx.stroke();
        ctx.fillStyle = textColor; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
        ctx.fillText(Math.round(maxVal * gi / 4) + 'ms', pad.left - 4, gy + 3);
    }

    // X labels
    ctx.fillStyle = textColor; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    var labelStep = Math.max(1, Math.floor(allTimes.length / 8));
    for (var xi = 0; xi < allTimes.length; xi += labelStep) {
        var xx = pad.left + (xi / Math.max(1, allTimes.length - 1)) * cW;
        var tLabel = allTimes[xi].substring(5, 13).replace('T', ' ');
        ctx.fillText(tLabel, xx, H - 4);
    }

    // Draw lines
    ctx.lineWidth = 2;
    for (var si = 0; si < seriesKeys.length; si++) {
        var sKey = seriesKeys[si];
        if (_latencyChartHidden[sKey]) continue;
        var parts = sKey.split('|');
        var prov = parts[0], metric = parts[1];
        var pIdx = providerKeys.indexOf(prov);
        var color = _LATENCY_COLORS[(pIdx >= 0 ? pIdx : si) % _LATENCY_COLORS.length];
        var mStyle = _METRIC_STYLES[metric] || [1.0, [5, 0]];

        ctx.strokeStyle = color;
        ctx.globalAlpha = mStyle[0];
        ctx.setLineDash(mStyle[1]);
        ctx.beginPath();
        var started = false;
        for (var ti = 0; ti < allTimes.length; ti++) {
            var val = series[sKey][allTimes[ti]];
            if (val == null) continue;
            var px = pad.left + (ti / Math.max(1, allTimes.length - 1)) * cW;
            var py = pad.top + cH - (val / maxVal) * cH;
            if (!started) { ctx.moveTo(px, py); started = true; } else { ctx.lineTo(px, py); }
        }
        ctx.stroke();
        ctx.globalAlpha = 1.0;
        ctx.setLineDash([]);
    }

    // Build interactive legend
    legendEl.innerHTML = '';
    for (var pi = 0; pi < providerKeys.length; pi++) {
        var prov = providerKeys[pi];
        var color = _LATENCY_COLORS[pi % _LATENCY_COLORS.length];
        for (var mi = 0; mi < metrics.length; mi++) {
            var metric = metrics[mi];
            var lKey = prov + '|' + metric;
            var hidden = _latencyChartHidden[lKey];
            var mStyle = _METRIC_STYLES[metric];
            var dashLabel = metric === 'avg' ? 'solid' : (metric === 'median' ? 'dashed' : 'dotted');
            var span = document.createElement('span');
            span.style.cssText = 'display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;padding:2px 6px;border-radius:4px;' +
                (hidden ? 'opacity:0.35;text-decoration:line-through;' : '');
            span.innerHTML = '<span style="width:18px;height:3px;display:inline-block;background:' + color + ';opacity:' + mStyle[0] + ';border-' +
                (dashLabel === 'dashed' ? 'style:dashed;border-width:0 0 3px 0;height:0;background:none;border-color:' + color :
                 dashLabel === 'dotted' ? 'style:dotted;border-width:0 0 3px 0;height:0;background:none;border-color:' + color : '') +
                '"></span>' + escapeHtml(prov) + ' ' + metric;
            span.dataset.key = lKey;
            span.addEventListener('click', function() {
                var k = this.dataset.key;
                _latencyChartHidden[k] = !_latencyChartHidden[k];
                _renderLatencyLineChart(timeSeries, providerKeys);
            });
            legendEl.appendChild(span);
        }
    }
}

// ---- Trend Chart ----
async function loadTrends() {
    try {
        const trends = await fetchApi('/api/dashboard/trends?days=7');
        renderBarChart(trends);
    } catch (error) {
        const chart = document.getElementById('trendChart');
        if (chart) {
            chart.innerHTML = '<div class="empty-state"><p>Could not load trend data</p></div>';
        }
    }
}

function renderBarChart(trends) {
    const chart = document.getElementById('trendChart');
    if (!chart || !trends.length) return;

    const maxTotal = Math.max(...trends.map(t => t.approved + t.denied), 1);

    chart.innerHTML = trends.map(t => {
        const approvedH = ((t.approved / maxTotal) * 100);
        const deniedH = ((t.denied / maxTotal) * 100);
        const dayLabel = new Date(t.date).toLocaleDateString(undefined, { weekday: 'short' });

        return `
            <div class="bar-group" title="${escapeHtml(t.date)}: ${t.approved} approved, ${t.denied} denied">
                <div class="bar-stack">
                    <div class="bar denied" style="height: ${deniedH}%"></div>
                    <div class="bar approved" style="height: ${approvedH}%"></div>
                </div>
                <span class="bar-label">${dayLabel}</span>
            </div>
        `;
    }).join('');
}

// ---- Score Distribution ----
async function loadScoreDistribution(events) {
    const donut = document.getElementById('scoreDonut');
    if (!donut) return;

    // If no events passed, fetch activity data for the chart
    if (!events) {
        try {
            events = await fetchApi('/api/dashboard/activity?limit=200');
        } catch {
            return;
        }
    }

    const scored = events.filter(e => e.safetyScore != null);
    if (scored.length === 0) {
        donut.style.background = 'var(--border-color)';
        const center = donut.querySelector('.donut-center');
        if (center) {
            center.innerHTML = '0<small>events</small>';
        }
        document.getElementById('safeCount').textContent = '0';
        document.getElementById('cautiousCount').textContent = '0';
        document.getElementById('riskyCount').textContent = '0';
        return;
    }

    const safe = scored.filter(e => e.safetyScore >= 70).length;
    const cautious = scored.filter(e => e.safetyScore >= 40 && e.safetyScore < 70).length;
    const risky = scored.filter(e => e.safetyScore < 40).length;
    const total = scored.length;

    const safeDeg = (safe / total) * 360;
    const cautiousDeg = safeDeg + (cautious / total) * 360;

    // Build explicit conic-gradient to avoid stale CSS variable issues
    var segments = [];
    if (safe > 0) segments.push('var(--color-success) 0deg ' + safeDeg + 'deg');
    if (cautious > 0) segments.push('var(--color-warning) ' + safeDeg + 'deg ' + cautiousDeg + 'deg');
    if (risky > 0) segments.push('var(--color-danger) ' + cautiousDeg + 'deg 360deg');
    // Handle single-category: fill the whole circle
    if (segments.length === 0) {
        donut.style.background = 'var(--border-color)';
    } else if (safe === total) {
        donut.style.background = 'var(--color-success)';
    } else if (cautious === total) {
        donut.style.background = 'var(--color-warning)';
    } else if (risky === total) {
        donut.style.background = 'var(--color-danger)';
    } else {
        donut.style.background = 'conic-gradient(' + segments.join(', ') + ')';
    }

    const center = donut.querySelector('.donut-center');
    if (center) {
        center.innerHTML = `${total}<small>events</small>`;
    }

    document.getElementById('safeCount').textContent = safe;
    document.getElementById('cautiousCount').textContent = cautious;
    document.getElementById('riskyCount').textContent = risky;
}

function showErrorState(id, title, message) {
    const el = document.getElementById(id);
    if (el) {
        el.innerHTML = `
            <div class="error-state">
                <h3>${escapeHtml(title)}</h3>
                <p>${escapeHtml(message)}</p>
                <button class="btn" onclick="refreshData()">Retry</button>
            </div>
        `;
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML.replace(/'/g, '&#39;');
}

// ===========================================================================
// Creative Features: Profile Switcher, Insights, Adaptive Thresholds, Quick Actions
// ===========================================================================

// ---- Profile Switcher ----
function initProfileSwitcher() {
    const cards = document.querySelectorAll('.profile-card');
    cards.forEach(function (card) {
        card.addEventListener('click', function () {
            const profileKey = this.getAttribute('data-profile');
            fetchApi('/api/profile/switch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profileKey: profileKey })
            })
                .then(function (data) {
                    cards.forEach(function (c) { c.classList.remove('active'); });
                    card.classList.add('active');
                    Toast.show('Profile Switched', 'Switched to ' + data.profile.name + ' profile', 'success');
                })
                .catch(function (err) {
                    Toast.show('Profile Error', 'Failed to switch profile: ' + err.message, 'danger');
                });
        });
    });

    // Load active profile
    fetchApi('/api/profile')
        .then(function (data) {
            cards.forEach(function (c) { c.classList.remove('active'); });
            const activeCard = document.querySelector('[data-profile="' + data.activeProfile + '"]');
            if (activeCard) activeCard.classList.add('active');
        })
        .catch(function () { /* silently fail on initial load */ });
}

// ---- Insights Panel ----
function loadInsights() {
    fetchApi('/api/insights')
        .then(function (data) {
            const list = document.getElementById('insightsList');
            const countEl = document.getElementById('insightCount');
            if (!list || !countEl) return;

            countEl.textContent = data.count;

            if (data.insights.length === 0) {
                list.innerHTML = '<p class="empty-state">No insights yet. Keep using the analyzer to generate recommendations.</p>';
                return;
            }

            list.innerHTML = '';
            data.insights.forEach(function (insight) {
                const item = document.createElement('div');
                item.className = 'insight-item severity-' + insight.severity;
                item.innerHTML =
                    '<div class="insight-content">' +
                    '<div class="insight-title">' + escapeHtml(insight.title) + '</div>' +
                    '<div class="insight-desc">' + escapeHtml(insight.description) + '</div>' +
                    '<div class="insight-recommendation">' + escapeHtml(insight.recommendation) + '</div>' +
                    '</div>' +
                    '<button class="insight-dismiss" data-id="' + escapeHtml(insight.id) + '" title="Dismiss">&times;</button>';
                list.appendChild(item);
            });

            // Bind dismiss buttons
            list.querySelectorAll('.insight-dismiss').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    const id = this.getAttribute('data-id');
                    fetchApi('/api/insights/dismiss/' + encodeURIComponent(id), { method: 'POST' })
                        .then(function () {
                            btn.closest('.insight-item').remove();
                            const current = parseInt(countEl.textContent) || 0;
                            countEl.textContent = Math.max(0, current - 1);
                        });
                });
            });
        })
        .catch(function () { /* silently fail */ });
}

// ---- Adaptive Thresholds Display ----
function loadAdaptiveStats() {
    fetchApi('/api/adaptivethreshold/stats')
        .then(function (data) {
            const grid = document.getElementById('adaptiveStats');
            if (!grid) return;

            if (!data.toolStats || data.toolStats.length === 0) {
                grid.innerHTML = '<p class="empty-state">No tool data yet. Decisions will appear here as the analyzer processes requests.</p>';
                return;
            }

            grid.innerHTML = '';
            data.toolStats.forEach(function (stat) {
                const confidencePct = Math.round(stat.confidenceLevel * 100);
                const scoreColor = stat.averageSafetyScore >= 90 ? 'var(--color-success)' :
                    stat.averageSafetyScore >= 70 ? 'var(--color-warning)' : 'var(--color-danger)';

                const card = document.createElement('div');
                card.className = 'adaptive-card';
                card.innerHTML =
                    '<div class="adaptive-tool-name">' + escapeHtml(stat.toolName) + '</div>' +
                    '<div class="adaptive-stats-row"><span>Decisions</span><span>' + stat.totalDecisions + '</span></div>' +
                    '<div class="adaptive-stats-row"><span>Overrides</span><span>' + stat.overrideCount + '</span></div>' +
                    '<div class="adaptive-stats-row"><span>Avg Score</span><span style="color:' + scoreColor + '">' + stat.averageSafetyScore + '</span></div>' +
                    '<div class="adaptive-bar"><div class="adaptive-bar-fill" style="width:' + confidencePct + '%;background:' + scoreColor + '"></div></div>' +
                    '<div class="adaptive-stats-row"><span>Confidence</span><span>' + confidencePct + '%</span></div>' +
                    (stat.suggestedThreshold != null ?
                        '<div class="adaptive-suggestion">Suggested threshold: ' + stat.suggestedThreshold + '</div>' : '');
                grid.appendChild(card);
            });
        })
        .catch(function () { /* silently fail */ });
}

// ---- Hooks Management ----
var currentEnforcementMode = 'observe';

async function loadHooksStatus() {
    try {
        var data = await fetchApi('/api/hooks/status');
        var mode = data.enforcementMode || (data.enforced ? 'enforce' : 'observe');
        currentEnforcementMode = mode;
        updateHooksUI(data.installed);
        updateEnforcementModeUI(mode);
        updateCopilotHooksUI(data.copilot);

        // Show warning banner if hooks not installed because user previously uninstalled them
        var banner = document.getElementById('hooksWarningBanner');
        if (banner) {
            if (!data.installed && data.hooksUserUninstalled) {
                banner.style.display = '';
            } else {
                banner.style.display = 'none';
            }
        }
    } catch {
        // Service may not support hooks endpoint yet
    }
}

function updateHooksUI(installed) {
    var installedBadge = document.getElementById('hookInstalledBadge');
    var btnToggleHooks = document.getElementById('btnToggleHooks');

    if (installedBadge) {
        installedBadge.textContent = installed ? 'Installed' : 'Not Installed';
        installedBadge.className = 'hook-status-badge ' + (installed ? 'badge-success' : 'badge-neutral');
    }
    if (btnToggleHooks) {
        btnToggleHooks.textContent = installed ? 'Uninstall Hooks' : 'Install Hooks';
        btnToggleHooks.disabled = false;
    }
}

function updateEnforcementModeUI(mode) {
    var cards = document.querySelectorAll('.enforcement-mode-card');
    cards.forEach(function (card) {
        card.classList.remove('active', 'switching');
    });
    var activeCard = document.querySelector('.enforcement-mode-card[data-mode="' + mode + '"]');
    if (activeCard) activeCard.classList.add('active');

    // Update connector fill to show progress
    var fill = document.getElementById('enforcementConnectorFill');
    if (fill) {
        var fillPct = { 'observe': '0', 'approve-only': '50', 'enforce': '100' };
        fill.style.width = (fillPct[mode] || '0') + '%';
    }

    // Show/hide "LLM analysis" toggle under observe mode
    var analyzeToggle = document.getElementById('observeAnalyzeToggle');
    if (analyzeToggle) {
        analyzeToggle.style.display = (mode === 'observe') ? '' : 'none';
    }
}

function initEnforcementModeCards() {
    var cards = document.querySelectorAll('.enforcement-mode-card');
    cards.forEach(function (card) {
        card.addEventListener('click', function () {
            var targetMode = this.getAttribute('data-mode');
            if (targetMode === currentEnforcementMode) return;

            // Visual feedback: mark all cards as switching
            cards.forEach(function (c) { c.classList.add('switching'); });

            fetchApi('/api/hooks/enforce?mode=' + encodeURIComponent(targetMode), { method: 'POST' })
                .then(function (data) {
                    currentEnforcementMode = data.enforcementMode;
                    updateEnforcementModeUI(data.enforcementMode);
                    Toast.show('Enforcement Mode', data.message, 'success');
                })
                .catch(function (err) {
                    Toast.show('Mode Error', 'Failed: ' + err.message, 'danger');
                    cards.forEach(function (c) { c.classList.remove('switching'); });
                });
        });
    });
}

function updateCopilotHooksUI(copilotStatus) {
    var badge = document.getElementById('copilotInstalledBadge');
    var btn = document.getElementById('btnToggleCopilotHooks');

    if (!copilotStatus) {
        if (badge) { badge.textContent = 'Unknown'; badge.className = 'hook-status-badge badge-neutral'; }
        if (btn) { btn.textContent = 'Install Hooks'; btn.disabled = false; }
        return;
    }

    var installed = copilotStatus.userInstalled;
    if (badge) {
        badge.textContent = installed ? 'Installed' : 'Not Installed';
        badge.className = 'hook-status-badge ' + (installed ? 'badge-copilot' : 'badge-neutral');
    }
    if (btn) {
        btn.textContent = installed ? 'Uninstall Hooks' : 'Install Hooks';
        btn.disabled = false;
    }
}

function initHooksControls() {
    var btnWarningInstall = document.getElementById('hooksWarningInstall');
    if (btnWarningInstall) {
        btnWarningInstall.addEventListener('click', function () {
            btnWarningInstall.disabled = true;
            fetchApi('/api/hooks/install', { method: 'POST' })
                .then(function (data) {
                    Toast.show('Hooks', data.message, 'success');
                    loadHooksStatus();
                })
                .catch(function (err) {
                    Toast.show('Hooks Error', 'Failed: ' + err.message, 'danger');
                    btnWarningInstall.disabled = false;
                });
        });
    }

    var btnToggleHooks = document.getElementById('btnToggleHooks');

    if (btnToggleHooks) {
        btnToggleHooks.addEventListener('click', function () {
            var isInstalled = document.getElementById('hookInstalledBadge').textContent === 'Installed';
            var endpoint = isInstalled ? '/api/hooks/uninstall' : '/api/hooks/install';
            btnToggleHooks.disabled = true;
            fetchApi(endpoint, { method: 'POST' })
                .then(function (data) {
                    Toast.show('Hooks', data.message, 'success');
                    loadHooksStatus();
                })
                .catch(function (err) {
                    Toast.show('Hooks Error', 'Failed: ' + err.message, 'danger');
                    btnToggleHooks.disabled = false;
                });
        });
    }

    // Install All Hooks button
    var btnInstallAll = document.getElementById('btnInstallAllHooks');
    if (btnInstallAll) {
        btnInstallAll.addEventListener('click', function () {
            btnInstallAll.disabled = true;
            btnInstallAll.textContent = 'Installing...';
            var providers = [
                { name: 'Claude', endpoint: '/api/hooks/install' },
                { name: 'Copilot', endpoint: '/api/hooks/copilot/install' },
            ];
            Promise.allSettled(
                providers.map(function (p) { return fetchApi(p.endpoint, { method: 'POST' }); })
            ).then(function (results) {
                var messages = results.map(function (r, i) {
                    return providers[i].name + ': ' + (r.status === 'fulfilled'
                        ? (r.value.message || 'installed')
                        : 'failed' + (r.reason ? ' - ' + r.reason.message : ''));
                });
                var allOk = results.every(function (r) { return r.status === 'fulfilled'; });
                Toast.show('Install All Hooks', messages.join(' | '), allOk ? 'success' : 'warning');
                loadHooksStatus();
            }).finally(function () {
                btnInstallAll.disabled = false;
                btnInstallAll.textContent = 'Install All Hooks';
            });
        });
    }

    // Copilot hooks controls
    var btnToggleCopilot = document.getElementById('btnToggleCopilotHooks');
    if (btnToggleCopilot) {
        btnToggleCopilot.addEventListener('click', function () {
            var badge = document.getElementById('copilotInstalledBadge');
            var isInstalled = badge && badge.textContent === 'Installed';
            var endpoint = isInstalled ? '/api/hooks/copilot/uninstall' : '/api/hooks/copilot/install';

            btnToggleCopilot.disabled = true;
            fetchApi(endpoint, { method: 'POST' })
                .then(function (data) {
                    Toast.show('Copilot Hooks', data.message, 'success');
                    loadHooksStatus();
                })
                .catch(function (err) {
                    Toast.show('Copilot Hooks Error', 'Failed: ' + err.message, 'danger');
                    btnToggleCopilot.disabled = false;
                });
        });
    }
}

// ---- Auto-refresh ----
let refreshInterval;

function initAnalyzeInObserveToggle() {
    var chk = document.getElementById('chkAnalyzeInObserve');
    if (!chk) return;

    // Load initial state
    fetchApi('/api/config/analyze-in-observe')
        .then(function (data) {
            chk.checked = data.analyzeInObserveMode !== false;
        })
        .catch(function () {});

    // Prevent click on the checkbox from toggling the enforcement mode card
    chk.addEventListener('click', function (e) { e.stopPropagation(); });
    var label = document.getElementById('observeAnalyzeToggle');
    if (label) label.addEventListener('click', function (e) { e.stopPropagation(); });

    chk.addEventListener('change', function () {
        var enabled = this.checked;
        fetchApi('/api/config/analyze-in-observe', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ analyzeInObserveMode: enabled }),
        })
            .then(function () {
                Toast.show('Observe Mode', enabled ? 'LLM analysis enabled' : 'LLM analysis disabled (log-only)', 'success');
            })
            .catch(function (err) {
                Toast.show('Error', 'Failed: ' + err.message, 'danger');
            });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    // Initialize features
    initProfileSwitcher();
    initHooksControls();
    initEnforcementModeCards();
    initAnalyzeInObserveToggle();

    // Load all data
    refreshData();
    refreshInterval = setInterval(refreshData, 30000);
});

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    } else {
        refreshData();
        if (refreshInterval) clearInterval(refreshInterval);
        refreshInterval = setInterval(refreshData, 30000);
    }
});
