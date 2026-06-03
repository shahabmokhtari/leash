/* ==========================================================================
   Shared Utilities - Theme, Toasts, Keyboard Shortcuts, Connection Status
   ========================================================================== */

// ---- Theme Management ----
const Theme = {
    STORAGE_KEY: 'cpa-theme',

    init() {
        const saved = localStorage.getItem(this.STORAGE_KEY);
        if (saved) {
            this.set(saved);
        } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
            this.set('dark');
        }

        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
            if (!localStorage.getItem(this.STORAGE_KEY)) {
                this.set(e.matches ? 'dark' : 'light');
            }
        });
    },

    set(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(this.STORAGE_KEY, theme);
        const btn = document.getElementById('themeToggle');
        if (btn) {
            btn.textContent = theme === 'dark' ? '\u2600' : '\u263E';
            btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
        }
    },

    toggle() {
        const current = document.documentElement.getAttribute('data-theme');
        this.set(current === 'dark' ? 'light' : 'dark');
    }
};

// ---- Toast Notifications ----
const Toast = {
    container: null,

    init() {
        this.container = document.getElementById('toastContainer');
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.id = 'toastContainer';
            this.container.className = 'toast-container';
            this.container.setAttribute('role', 'status');
            this.container.setAttribute('aria-live', 'polite');
            document.body.appendChild(this.container);
        }
    },

    show(title, message, type = 'info', duration = 5000) {
        if (!this.container) this.init();

        const icons = {
            success: '\u2713',
            danger: '\u2717',
            warning: '\u26A0',
            info: '\u2139'
        };

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.setAttribute('role', 'alert');
        toast.innerHTML = `
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <div class="toast-body">
                <div class="toast-title">${this.escapeHtml(title)}</div>
                <div class="toast-message">${this.escapeHtml(message)}</div>
            </div>
            <button class="toast-close" aria-label="Dismiss notification">&times;</button>
        `;

        toast.querySelector('.toast-close').addEventListener('click', () => this.remove(toast));
        this.container.appendChild(toast);

        if (duration > 0) {
            setTimeout(() => this.remove(toast), duration);
        }

        return toast;
    },

    remove(toast) {
        if (!toast || !toast.parentNode) return;
        toast.classList.add('removing');
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
    },

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML.replace(/'/g, '&#39;');
    }
};

// ---- Connection Status ----
const ConnectionStatus = {
    indicator: null,
    label: null,
    interval: null,

    init() {
        this.indicator = document.querySelector('.status-indicator');
        this.label = document.querySelector('.status-label');
        this.check();
        this.interval = setInterval(() => this.check(), 15000);
    },

    async check() {
        try {
            const response = await fetch('/api/dashboard/health', { signal: AbortSignal.timeout(5000) });
            if (response.ok) {
                this.setStatus(true);
            } else {
                this.setStatus(false);
            }
        } catch {
            this.setStatus(false);
        }
    },

    setStatus(connected) {
        if (this.indicator) {
            this.indicator.classList.toggle('active', connected);
            this.indicator.classList.toggle('disconnected', !connected);
        }
        if (this.label) {
            this.label.textContent = connected ? 'Connected' : 'Disconnected';
        }
    }
};

// ---- Auto-Start Toggle ----
const AutoStart = {
    btn: null,
    enabled: false,

    init() {
        this.btn = document.getElementById('btnAutoStart');
        if (!this.btn) return;
        this.btn.addEventListener('click', () => this.toggle());
        this.checkStatus();
    },

    async checkStatus() {
        try {
            const data = await fetchApi('/api/hooks/session-start/status');
            this.enabled = data.installed;
            this.updateUI();
        } catch {
            // Non-fatal
        }
    },

    async toggle() {
        if (!this.btn) return;
        this.btn.disabled = true;
        try {
            const endpoint = this.enabled
                ? '/api/hooks/session-start/uninstall'
                : '/api/hooks/session-start/install';
            const data = await fetchApi(endpoint, { method: 'POST' });
            this.enabled = data.installed;
            this.updateUI();
            Toast.show(
                'Auto-Start',
                this.enabled
                    ? 'SessionStart hook installed — Leash will auto-start with Claude (Copilot user-level support is best-effort)'
                    : 'SessionStart hook removed',
                this.enabled ? 'success' : 'info'
            );
        } catch (err) {
            Toast.show('Auto-Start', err.message, 'danger');
        } finally {
            this.btn.disabled = false;
        }
    },

    updateUI() {
        if (!this.btn) return;
        this.btn.classList.toggle('active', this.enabled);
        this.btn.title = this.enabled
            ? 'Auto-start enabled (click to disable)'
            : 'Auto-start disabled (click to enable)';
    }
};

// ---- Keyboard Shortcuts ----
const Shortcuts = {
    modal: null,

    init() {
        this.modal = document.getElementById('shortcutsModal');

        document.addEventListener('keydown', (e) => {
            // Don't trigger shortcuts when typing in inputs
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
                if (e.key === 'Escape') {
                    e.target.blur();
                }
                return;
            }

            switch (e.key) {
                case '?':
                    e.preventDefault();
                    this.toggleModal();
                    break;
                case 'd':
                    e.preventDefault();
                    Theme.toggle();
                    break;
                case 'r':
                    e.preventDefault();
                    if (typeof refreshData === 'function') refreshData();
                    break;
                case '1':
                    e.preventDefault();
                    window.location.href = '/';
                    break;
                case '2':
                    e.preventDefault();
                    window.location.href = '/logs.html';
                    break;
                case '3':
                    e.preventDefault();
                    window.location.href = '/config.html';
                    break;
                case 't':
                    e.preventDefault();
                    TerminalPanel.toggle();
                    break;
                case 'Escape':
                    if (this.modal && this.modal.classList.contains('active')) {
                        this.toggleModal();
                    } else if (document.getElementById('terminalPanel')?.classList.contains('open')) {
                        TerminalPanel.close();
                    }
                    break;
            }
        });

        if (this.modal) {
            this.modal.addEventListener('click', (e) => {
                if (e.target === this.modal) {
                    this.toggleModal();
                }
            });
        }
    },

    toggleModal() {
        if (this.modal) {
            this.modal.classList.toggle('active');
        }
    }
};

// ---- Data Fetching with Error Handling ----
async function fetchApi(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            signal: options.signal || AbortSignal.timeout(10000)
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        return await response.json();
    } catch (error) {
        if (error.name === 'AbortError' || error.name === 'TimeoutError') {
            throw new Error('Request timed out. Please check your connection.');
        }
        throw error;
    }
}

// ---- Time Formatting ----
function formatTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;

    if (diff < 60000) return 'just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;

    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
        ' ' + date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function formatTimestamp(timestamp) {
    return new Date(timestamp).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// ---- Score Helpers ----
function getScoreClass(score) {
    if (score >= 70) return 'high';
    if (score >= 40) return 'medium';
    return 'low';
}

/**
 * Returns an inline CSS style string for a score relative to a threshold.
 * Below threshold: red gradient (darker red closer to 0).
 * At/above threshold: green gradient (darker green closer to 100).
 */
function getScoreColorStyle(score, threshold) {
    if (score == null) return '';
    if (threshold == null) threshold = 85;
    if (score < threshold) {
        // 0 = darkest red, threshold-1 = lightest red
        var t = Math.max(0, Math.min(score, threshold - 1)) / Math.max(threshold, 1);
        // Interpolate from dark red (153,0,0) to light red (239,154,154)
        var r = Math.round(153 + t * (239 - 153));
        var g = Math.round(0 + t * 100);
        var b = Math.round(0 + t * 100);
        return 'color:rgb(' + r + ',' + g + ',' + b + ');font-weight:700;';
    } else {
        // threshold = lightest green, 100 = darkest green
        var range = 100 - threshold;
        var t2 = range > 0 ? (score - threshold) / range : 1;
        // Interpolate from light green (129,199,132) to dark green (27,94,32)
        var r2 = Math.round(129 - t2 * (129 - 27));
        var g2 = Math.round(199 - t2 * (199 - 94));
        var b2 = Math.round(132 - t2 * (132 - 32));
        return 'color:rgb(' + r2 + ',' + g2 + ',' + b2 + ');font-weight:700;';
    }
}

function getDecisionClass(decision) {
    switch (decision) {
        case 'auto-approved':
        case 'tray-approved':
        case 'script-approved': return 'approved';
        case 'denied':
        case 'tray-denied':
        case 'script-denied': return 'denied';
        case 'tray-ignored': return 'logged';
        case 'tray-timeout': return 'logged';
        case 'logged':
        case 'no-handler': return 'logged';
        default: return 'logged';
    }
}

function getDecisionLabel(decision) {
    switch (decision) {
        case 'auto-approved': return 'Approved';
        case 'denied': return 'Denied';
        case 'tray-approved': return 'Tray Approved';
        case 'tray-denied': return 'Tray Denied';
        case 'tray-ignored': return 'Tray Ignored';
        case 'tray-timeout': return 'Tray Timeout';
        case 'script-approved': return 'Script Approved';
        case 'script-denied': return 'Script Denied';
        case 'logged':
        case 'no-handler': return 'Logged';
        default: return decision || 'Unknown';
    }
}

// ---- Simple Markdown Renderer (no external deps) ----
const SimpleMarkdown = {
    render(text) {
        if (!text) return '';
        var html = this.escapeHtml(text);

        // Code blocks (``` ... ```) - must be before inline patterns
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
            return '<pre class="md-code-block"><code>' + code.trim() + '</code></pre>';
        });

        // Inline code
        html = html.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');

        // Headings
        html = html.replace(/^#### (.+)$/gm, '<h4 class="md-h4">$1</h4>');
        html = html.replace(/^### (.+)$/gm, '<h3 class="md-h3">$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2 class="md-h2">$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1 class="md-h1">$1</h1>');

        // Bold and italic
        html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Unordered lists
        html = html.replace(/^[\-\*] (.+)$/gm, '<li class="md-li">$1</li>');
        html = html.replace(/((?:<li class="md-li">.*<\/li>\n?)+)/g, '<ul class="md-ul">$1</ul>');

        // Ordered lists
        html = html.replace(/^\d+\. (.+)$/gm, '<li class="md-oli">$1</li>');
        html = html.replace(/((?:<li class="md-oli">.*<\/li>\n?)+)/g, '<ol class="md-ol">$1</ol>');

        // Links [text](url) - only allow safe protocols
        html = html.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\)]+)\)/g, '<a href="$2" class="md-link" target="_blank" rel="noopener">$1</a>');

        // Horizontal rules
        html = html.replace(/^---+$/gm, '<hr class="md-hr">');

        // Line breaks (preserve paragraph breaks)
        html = html.replace(/\n\n/g, '</p><p class="md-p">');
        html = '<p class="md-p">' + html + '</p>';
        // Clean up empty paragraphs
        html = html.replace(/<p class="md-p"><\/p>/g, '');
        // Don't wrap block elements in p
        html = html.replace(/<p class="md-p">(<(?:h[1-4]|pre|ul|ol|hr)[^>]*>)/g, '$1');
        html = html.replace(/(<\/(?:h[1-4]|pre|ul|ol|hr)>)<\/p>/g, '$1');

        return html;
    },

    escapeHtml(str) {
        return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
};

// ---- Terminal Panel (sticky bottom) ----
const TerminalPanel = {
    STORAGE_KEY: 'cpa-terminal-open',
    HEIGHT_KEY: 'cpa-terminal-height',
    MAX_LINES: 2000,
    MIN_HEIGHT: 120,
    MAX_HEIGHT_RATIO: 0.6,
    panel: null,
    outputEl: null,
    eventSource: null,
    autoScroll: true,
    resizing: false,
    _lastSeqId: 0,

    init() {
        // Inject bottom panel DOM
        const panel = document.createElement('div');
        panel.className = 'terminal-panel';
        panel.id = 'terminalPanel';
        panel.innerHTML = `
            <div class="terminal-resize-handle" id="terminalResizeHandle"></div>
            <div class="terminal-header" id="terminalHeader">
                <h3><span class="terminal-icon">&#9002;</span> LLM Process Output</h3>
                <div class="terminal-header-actions">
                    <label><input type="checkbox" id="terminalAutoScroll" checked> Auto-scroll</label>
                    <button class="terminal-btn-sm" id="terminalClear">Clear</button>
                    <button class="terminal-close" id="terminalClose" aria-label="Close terminal">&times;</button>
                </div>
            </div>
            <div class="terminal-output" id="terminalOutput">
                <div class="terminal-empty">No subprocess output yet</div>
            </div>
        `;
        document.body.appendChild(panel);
        this.panel = panel;
        this.outputEl = panel.querySelector('#terminalOutput');

        // Inject toggle button into nav
        const nav = document.querySelector('nav .nav-links, nav');
        if (nav) {
            const btn = document.createElement('button');
            btn.className = 'terminal-toggle-btn';
            btn.id = 'terminalToggle';
            btn.textContent = 'Terminal';
            btn.setAttribute('aria-label', 'Toggle terminal panel');
            btn.addEventListener('click', () => this.toggle());
            nav.appendChild(btn);
        }

        // Wire up actions
        panel.querySelector('#terminalClose').addEventListener('click', () => this.close());
        panel.querySelector('#terminalClear').addEventListener('click', () => this.clear());
        panel.querySelector('#terminalAutoScroll').addEventListener('change', (e) => {
            this.autoScroll = e.target.checked;
        });

        // Resize via drag handle or header
        this._initResize(panel.querySelector('#terminalResizeHandle'));
        this._initResize(panel.querySelector('#terminalHeader'));

        // Restore open/closed state (open() applies the saved height)
        if (localStorage.getItem(this.STORAGE_KEY) === 'true') {
            this.open();
        }

        // Always connect SSE so buffer fills even when panel is closed
        this.connect();
    },

    _initResize(handle) {
        if (!handle) return;
        let startY, startH;

        const onMouseMove = (e) => {
            if (!this.resizing) return;
            const delta = startY - e.clientY;
            const newH = Math.max(this.MIN_HEIGHT, Math.min(window.innerHeight * this.MAX_HEIGHT_RATIO, startH + delta));
            this._applyHeight(newH);
        };

        const onMouseUp = () => {
            if (!this.resizing) return;
            this.resizing = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            // Save height
            const h = this.panel.offsetHeight;
            localStorage.setItem(this.HEIGHT_KEY, h);
        };

        handle.addEventListener('mousedown', (e) => {
            if (!this.panel.classList.contains('open')) return;
            this.resizing = true;
            startY = e.clientY;
            startH = this.panel.offsetHeight;
            document.body.style.cursor = 'ns-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
            e.preventDefault();
        });
    },

    _applyHeight(h) {
        this.panel.style.height = h + 'px';
        document.body.style.paddingBottom = h + 'px';
    },

    toggle() {
        if (this.panel.classList.contains('open')) {
            this.close();
        } else {
            this.open();
        }
    },

    open() {
        this.panel.classList.add('open');
        document.body.classList.add('terminal-open');
        // Apply saved height or default
        const savedH = parseInt(localStorage.getItem(this.HEIGHT_KEY)) || 240;
        this._applyHeight(savedH);
        localStorage.setItem(this.STORAGE_KEY, 'true');
        const btn = document.getElementById('terminalToggle');
        if (btn) btn.classList.add('active');
        if (this.autoScroll) this.scrollToBottom();
    },

    close() {
        this.panel.classList.remove('open');
        this.panel.style.height = '0';
        document.body.classList.remove('terminal-open');
        document.body.style.paddingBottom = '';
        localStorage.setItem(this.STORAGE_KEY, 'false');
        const btn = document.getElementById('terminalToggle');
        if (btn) btn.classList.remove('active');
    },

    _reconnectDelay: 1000,
    _maxReconnectDelay: 30000,

    connect() {
        // Disconnect existing SSE first to avoid duplicates on reconnect
        this.disconnect();

        // Fetch existing buffer first, THEN connect SSE to avoid race
        // where SSE messages arrive before buffer is processed.
        fetch('/api/terminal/buffer')
            .then(r => r.json())
            .then(lines => {
                if (lines && lines.length > 0) {
                    const empty = this.outputEl.querySelector('.terminal-empty');
                    if (empty) empty.remove();
                    lines.forEach(line => {
                        const seqId = line.sequence_id || 0;
                        if (seqId > this._lastSeqId) {
                            this._lastSeqId = seqId;
                            this.appendLine(line);
                        }
                    });
                }
            })
            .catch((err) => { console.warn('Terminal buffer fetch failed:', err); })
            .finally(() => { this._connectSse(); });
    },

    _connectSse() {
        this.eventSource = new EventSource('/api/terminal/stream');
        this.eventSource.onmessage = (event) => {
            try {
                const line = JSON.parse(event.data);
                const seqId = line.sequence_id || 0;
                if (seqId > this._lastSeqId) {
                    this._lastSeqId = seqId;
                    this.appendLine(line);
                }
                // Reset backoff on successful message
                this._reconnectDelay = 1000;
            } catch (err) { console.warn('Terminal SSE parse error:', err); }
        };
        this.eventSource.onerror = () => {
            if (this.eventSource) {
                this.eventSource.close();
                this.eventSource = null;
            }
            // Exponential backoff with cap
            var delay = this._reconnectDelay;
            this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
            setTimeout(() => this.connect(), delay);
        };
    },

    disconnect() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    },

    _levelIcon(level) {
        switch (level) {
            case 'stderr': return '\u2716';  // ✖
            case 'info':   return '\u2139';  // ℹ
            default:       return '\u25B8';  // ▸
        }
    },

    _formatText(text) {
        // Highlight [PID nnn] tokens
        let html = this.escapeHtml(text);
        html = html.replace(/\[PID\s+(\d+)\]/g, '<span class="terminal-pid">PID $1</span>');
        // Highlight score=NN
        html = html.replace(/score=(\d+)/g, (m, score) => {
            const n = parseInt(score);
            const cls = n >= 80 ? 'terminal-score-safe' : n >= 50 ? 'terminal-score-cautious' : 'terminal-score-danger';
            return `<span class="${cls}">score=${score}</span>`;
        });
        // Highlight category=xxx
        html = html.replace(/category=(safe|cautious|risky|dangerous|unknown)/g, (m, cat) => {
            const cls = 'terminal-cat-' + cat;
            return `category=<span class="${cls}">${cat}</span>`;
        });
        // Highlight timing like "in NNNms" or "NNNms"
        html = html.replace(/\b(\d+)ms\b/g, '<span class="terminal-timing">$1ms</span>');
        return html;
    },

    appendLine(line) {
        const empty = this.outputEl.querySelector('.terminal-empty');
        if (empty) empty.remove();

        const div = document.createElement('div');
        const level = line.level || 'stdout';
        div.className = `terminal-line terminal-level-${this.escapeAttr(level)}`;

        const ts = new Date(line.timestamp);
        const timeStr = ts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        const sourceClass = 'terminal-source-' + (line.source || '').replace(/[^a-z0-9-]/g, '');
        const icon = this._levelIcon(level);

        div.innerHTML =
            `<span class="terminal-level-icon terminal-level-icon-${this.escapeAttr(level)}">${icon}</span>` +
            `<span class="terminal-ts">${this.escapeHtml(timeStr)}</span>` +
            `<span class="terminal-source ${sourceClass}">${this.escapeHtml(line.source || '')}</span>` +
            `<span class="terminal-text">${this._formatText(line.text || '')}</span>`;

        this.outputEl.appendChild(div);

        while (this.outputEl.children.length > this.MAX_LINES) {
            this.outputEl.removeChild(this.outputEl.firstChild);
        }

        if (this.autoScroll) this.scrollToBottom();
    },

    scrollToBottom() {
        if (this.outputEl) {
            this.outputEl.scrollTop = this.outputEl.scrollHeight;
        }
    },

    clear() {
        fetch('/api/terminal/clear', { method: 'POST' })
            .then(() => {
                this.outputEl.innerHTML = '<div class="terminal-empty">No subprocess output yet</div>';
                this._lastSeqId = 0;
            })
            .catch(() => Toast.show('Error', 'Failed to clear terminal', 'danger'));
    },

    escapeHtml(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    },

    escapeAttr(str) {
        return String(str).replace(/[^a-z0-9-]/gi, '');
    }
};

// ---- Filter Persistence (localStorage) ----
function saveFilter(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
function loadFilter(key, fallback) {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    try { return JSON.parse(v); } catch { return fallback; }
}

// ---- Transcript Prefetch (background, all pages) ----
const _PREFETCH_STORAGE_KEY = 'leash-transcript-projects';
const _PREFETCH_MAX_AGE_MS = 60000; // 60s freshness window

const TranscriptPrefetch = {
    prefetch() {
        fetch('/api/transcripts/projects', { signal: AbortSignal.timeout(15000) })
            .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
            .then(data => {
                try {
                    sessionStorage.setItem(_PREFETCH_STORAGE_KEY, JSON.stringify({
                        ts: Date.now(), data: data
                    }));
                } catch (e) { console.debug('TranscriptPrefetch: sessionStorage write failed:', e.message); }
            })
            .catch(err => { console.debug('TranscriptPrefetch: prefetch failed:', err); });
    },

    getCached() {
        try {
            var raw = sessionStorage.getItem(_PREFETCH_STORAGE_KEY);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            if (Date.now() - parsed.ts > _PREFETCH_MAX_AGE_MS) return null;
            return parsed.data;
        } catch (e) {
            console.debug('TranscriptPrefetch: cache read failed, clearing:', e.message);
            sessionStorage.removeItem(_PREFETCH_STORAGE_KEY);
            return null;
        }
    },

    clear() {
        sessionStorage.removeItem(_PREFETCH_STORAGE_KEY);
    }
};

// ---- Initialize on DOM load ----
document.addEventListener('DOMContentLoaded', () => {
    Theme.init();
    Toast.init();
    ConnectionStatus.init();
    Shortcuts.init();
    AutoStart.init();
    TerminalPanel.init();

    const themeBtn = document.getElementById('themeToggle');
    if (themeBtn) {
        themeBtn.addEventListener('click', () => Theme.toggle());
    }

    // Shutdown button (present on all pages)
    const btnShutdown = document.getElementById('btnShutdown');
    if (btnShutdown) {
        btnShutdown.addEventListener('click', () => {
            if (!confirm('Are you sure you want to shut down Leash? Hooks will be uninstalled and all sessions will stop being monitored.')) return;
            btnShutdown.disabled = true;
            fetchApi('/api/shutdown', { method: 'POST' })
                .then(() => Toast.show('Shutdown', 'Leash is shutting down...', 'info'))
                .catch(() => Toast.show('Shutdown', 'Leash is shutting down...', 'info'));
        });
    }

    // Prefetch transcript projects only on pages that use them
    if (/\/(transcripts|logs)(\.html)?/i.test(location.pathname) || location.pathname === '/') {
        TranscriptPrefetch.prefetch();
    }
});
