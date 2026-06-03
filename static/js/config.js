/* ==========================================================================
   Config Page Logic
   ========================================================================== */

let currentConfig = null;
let isDirty = false;

/* Hook handlers local state - edited independently from the config form fields */
let hookHandlersDirty = false;

/* Debounce timers for auto-save */
var _configSaveTimer = null;
var _hookHandlersSaveTimer = null;
var _AUTO_SAVE_DELAY = 800; // ms
let promptTemplateNames = [];

/* Cached provider model/capability data from /api/config/provider-models */
let _providerModelsData = null;

const HOOK_EVENT_TYPES = [
    'PreToolUse',
    'PostToolUse',
    'PostToolUseFailure',
    'UserPromptSubmit',
    'Stop'
];

const HANDLER_MODES = [
    { value: 'llm-analysis', label: 'LLM Analysis' },
    { value: 'log-only', label: 'Log Only' },
    { value: 'context-injection', label: 'Context Injection' },
    { value: 'custom-logic', label: 'Custom Logic' }
];

const HOOK_EVENT_DESCRIPTIONS = {
    'PreToolUse': 'Pre-execution safety gate. Can allow, deny, or ask the user.',
    'PostToolUse': 'Post-execution validation. Can inject additional context.',
    'PostToolUseFailure': 'Handles tool execution failures. Typically log-only.',
    'UserPromptSubmit': 'Fires when a user submits a prompt. Typically log-only.',
    'Stop': 'Fires when a Claude session ends. Used for session cleanup.'
};

// Which harnesses support each hook event
const HOOK_EVENT_HARNESSES = {
    'PreToolUse': ['claude', 'copilot'],
    'PostToolUse': ['claude', 'copilot'],
    'PostToolUseFailure': ['claude'],
    'UserPromptSubmit': ['claude'],
    'PermissionRequest': ['claude'],
    'Stop': ['claude'],
};

function getHarnessIcons(eventType) {
    const harnesses = HOOK_EVENT_HARNESSES[eventType] || ['claude'];
    return harnesses.map(function(h) {
        if (h === 'claude') return '<span class="badge-claude" style="font-size:0.6em;" title="Claude Code">CL</span>';
        if (h === 'copilot') return '<span class="badge-copilot" style="font-size:0.6em;" title="Copilot CLI">CP</span>';
        return '<span style="font-size:0.6em;">' + h + '</span>';
    }).join(' ');
}

const MODE_BADGE_COLORS = {
    'llm-analysis': 'var(--color-info)',
    'log-only': 'var(--text-faint)',
    'context-injection': 'var(--color-warning)',
    'custom-logic': 'var(--color-success)'
};

async function refreshData() {
    await loadConfig();
}

async function loadConfig() {
    const container = document.getElementById('configContent');
    if (!container) return;

    try {
        currentConfig = await fetchApi('/api/config');
        renderConfig(currentConfig);
        isDirty = false;
        await loadPromptTemplates();
        renderHookHandlers();
        hookHandlersDirty = false;
    } catch (error) {
        container.innerHTML = `
            <div class="error-state">
                <h3>Failed to load configuration</h3>
                <p>${escapeHtml(error.message)}</p>
                <button class="btn" onclick="loadConfig()">Retry</button>
            </div>
        `;
    }
}

async function loadPromptTemplates() {
    try {
        const templates = await fetchApi('/api/prompts');
        if (templates && typeof templates === 'object') {
            promptTemplateNames = Object.keys(templates);
        } else {
            promptTemplateNames = [];
        }
    } catch (e) {
        promptTemplateNames = [];
    }
}

function renderConfig(config) {
    const container = document.getElementById('configContent');
    if (!container) return;

    container.innerHTML = `
        <div class="config-section">
            <h3>Server</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-host">
                    Host
                    <small>The hostname the server listens on</small>
                </label>
                <input id="cfg-host" class="config-input" type="text"
                    value="${escapeAttr(config.server?.host || 'localhost')}"
                    data-path="server.host" aria-label="Server host">
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-port">
                    Port
                    <small>TCP port for the HTTP API</small>
                </label>
                <input id="cfg-port" class="config-input" type="number"
                    value="${config.server?.port || 5050}" min="1024" max="65535"
                    data-path="server.port" aria-label="Server port">
            </div>
        </div>

        <div class="config-section">
            <h3>LLM Provider</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-provider">
                    Provider
                    <small>LLM backend used for safety analysis. One-shot providers spawn a new process per query. Stream and ACP providers keep a persistent process alive for lower latency on subsequent queries.</small>
                </label>
                <select id="cfg-provider" class="config-input"
                    data-path="llm.provider" aria-label="LLM provider"
                    onchange="updateProviderFields()">
                    <option value="anthropic-api" ${(config.llm?.provider || 'anthropic-api') === 'anthropic-api' ? 'selected' : ''}>Anthropic API (Direct HTTP)</option>
                    <option value="claude-cli" ${config.llm?.provider === 'claude-cli' ? 'selected' : ''}>Claude Code CLI (One-shot)</option>
                    <option value="claude-persistent" ${config.llm?.provider === 'claude-persistent' ? 'selected' : ''}>Claude Code CLI (ACP)</option>
                    <option value="claude-stream" ${config.llm?.provider === 'claude-stream' ? 'selected' : ''}>Claude Code CLI (Stream)</option>
                    <option value="copilot-cli" ${config.llm?.provider === 'copilot-cli' ? 'selected' : ''}>GitHub Copilot CLI (One-shot)</option>
                    <option value="copilot-persistent" ${config.llm?.provider === 'copilot-persistent' ? 'selected' : ''}>GitHub Copilot CLI (ACP)</option>
                    <option value="generic-rest" ${config.llm?.provider === 'generic-rest' ? 'selected' : ''}>Generic REST API</option>
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-model">
                    Model
                    <small>Select a model for the current provider. Each provider remembers its own model selection independently. Choose "Custom..." to enter a model ID not in the list.</small>
                </label>
                <select id="cfg-model" class="config-input"
                    data-path="llm.model" aria-label="LLM model"
                    onchange="onModelDropdownChange()">
                    <option value="${escapeAttr(config.llm?.model ?? '')}" selected>${escapeHtml(config.llm?.model || '(Default)')}</option>
                </select>
                <div id="custom-model-wrapper" style="display:none; margin-top: 6px;">
                    <input id="cfg-model-custom" class="config-input" type="text"
                        placeholder="Enter custom model ID"
                        aria-label="Custom model ID"
                        value="">
                </div>
            </div>
            <div class="config-field" id="effort-field" style="display:none;">
                <label class="config-label" for="cfg-effort">
                    Effort Level
                    <small>Controls how much reasoning effort the LLM uses. Low = faster and cheaper, High = more thorough analysis. Extra High is available for Copilot providers only. Leave as Default to let the provider decide.</small>
                </label>
                <select id="cfg-effort" class="config-input"
                    data-path="llm.effortLevel" aria-label="Effort level">
                    <!-- Populated dynamically from /api/config/provider-models -->
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-timeout">
                    Timeout (ms)
                    <small>Maximum time to wait for a single LLM query to complete, including any prefix/suffix messages. If queries consistently time out, increase this value. Range: 1,000–300,000 ms.</small>
                </label>
                <input id="cfg-timeout" class="config-input" type="number"
                    value="${config.llm?.timeout || 30000}" min="1000" max="300000"
                    data-path="llm.timeout" aria-label="LLM timeout">
            </div>

            <!-- Persistent provider fields -->
            <div id="provider-persistent" class="provider-fields">
                <div class="config-field">
                    <label class="config-label" for="cfg-idle-timeout">
                        Session Idle Timeout (minutes)
                        <small>Automatically terminate persistent CLI subprocesses after this many minutes of inactivity. Each idle process consumes memory and an OS process slot. Set to 0 to disable (processes live until server shutdown).</small>
                    </label>
                    <input id="cfg-idle-timeout" class="config-input" type="number"
                        value="${config.llm?.sessionIdleTimeoutMinutes ?? 5}" min="0" max="60"
                        data-path="llm.sessionIdleTimeoutMinutes" aria-label="Session idle timeout">
                </div>
                <div class="config-field">
                    <label class="config-label" for="cfg-max-queries">
                        Max Queries Per Session
                        <small>Number of queries before rotating the session (ACP) or restarting the process (Stream) to clear accumulated conversation context. Higher values mean fewer expensive restarts but more memory usage. Default: 100.</small>
                    </label>
                    <input id="cfg-max-queries" class="config-input" type="number"
                        value="${config.llm?.maxQueriesPerSession ?? 100}" min="1" max="10000"
                        data-path="llm.maxQueriesPerSession" aria-label="Max queries per session">
                </div>
                <div class="config-field">
                    <label class="config-label" for="cfg-max-sessions">
                        Max Concurrent Sessions
                        <small>Maximum number of simultaneous persistent CLI subprocesses (one per Claude/Copilot session). When this limit is reached, the oldest idle session is evicted. Each process uses ~50–100 MB of memory. Default: 20.</small>
                    </label>
                    <input id="cfg-max-sessions" class="config-input" type="number"
                        value="${config.llm?.maxConcurrentSessions ?? 20}" min="1" max="100"
                        data-path="llm.maxConcurrentSessions" aria-label="Max concurrent sessions">
                </div>
            </div>

            <!-- Anthropic API fields -->
            <div id="provider-anthropic-api" class="provider-fields">
                <div class="config-field">
                    <label class="config-label" for="cfg-apikey">
                        API Key
                        <small>Your Anthropic API key for direct HTTP access. If left empty, Leash falls back to the key stored in ~/.claude/config.json (primaryApiKey field).</small>
                    </label>
                    <input id="cfg-apikey" class="config-input" type="password"
                        value="${escapeAttr(config.llm?.apiKey || '')}"
                        placeholder="(uses Claude config key)"
                        data-path="llm.apiKey" aria-label="API key"
                        autocomplete="off">
                </div>
                <div class="config-field">
                    <label class="config-label" for="cfg-apibaseurl">
                        API Base URL
                        <small>Override the Anthropic API base URL. Use this when routing through a proxy, API gateway, or a compatible third-party API (e.g. AWS Bedrock, Azure). Default: https://api.anthropic.com</small>
                    </label>
                    <input id="cfg-apibaseurl" class="config-input" type="text"
                        value="${escapeAttr(config.llm?.apiBaseUrl || '')}"
                        placeholder="https://api.anthropic.com"
                        data-path="llm.apiBaseUrl" aria-label="API base URL">
                </div>
            </div>

            <!-- CLI provider fields -->
            <div id="provider-cli" class="provider-fields">
                <div class="config-field">
                    <label class="config-label" for="cfg-command">
                        CLI Command
                        <small>Override the CLI executable name. For Claude providers use "claude"; for Copilot use "copilot" or "gh" (auto-detected). Leave empty for auto-detection from PATH.</small>
                    </label>
                    <input id="cfg-command" class="config-input" type="text"
                        value="${escapeAttr(config.llm?.command || '')}"
                        placeholder="auto-detect"
                        data-path="llm.command" aria-label="CLI command">
                </div>
            </div>

            <!-- Generic REST fields -->
            <div id="provider-generic-rest" class="provider-fields">
                <div class="config-field">
                    <label class="config-label" for="cfg-rest-url">
                        REST URL
                        <small>Full endpoint URL for the LLM API. Must accept POST requests with a JSON body and return a JSON response. Example: https://api.openai.com/v1/chat/completions</small>
                    </label>
                    <input id="cfg-rest-url" class="config-input" type="text"
                        value="${escapeAttr(config.llm?.genericRest?.url || '')}"
                        placeholder="https://api.openai.com/v1/chat/completions"
                        data-path="llm.genericRest.url" aria-label="REST URL"
                        style="width: 350px;">
                </div>
                <div class="config-field">
                    <label class="config-label" for="cfg-rest-headers">
                        Headers (JSON)
                        <small>HTTP headers sent with every request, as a JSON object. Typically includes an Authorization header with your API key. Example: {"Authorization": "Bearer sk-..."}</small>
                    </label>
                    <textarea id="cfg-rest-headers" class="config-input" rows="3"
                        placeholder='{"Authorization": "Bearer sk-..."}'
                        data-path="llm.genericRest.headers" aria-label="REST headers"
                        style="width: 350px; font-family: var(--font-mono); font-size: 12px;">${escapeHtml(JSON.stringify(config.llm?.genericRest?.headers || {}, null, 2))}</textarea>
                </div>
                <div class="config-field">
                    <label class="config-label" for="cfg-rest-body">
                        Body Template
                        <small>JSON request body template. Use {PROMPT} as a placeholder for the safety analysis prompt. The placeholder is replaced with the actual prompt text before sending. Example: {"model":"gpt-4","messages":[{"role":"user","content":"{PROMPT}"}]}</small>
                    </label>
                    <textarea id="cfg-rest-body" class="config-input" rows="5"
                        placeholder='{"model":"gpt-4","messages":[{"role":"user","content":"{PROMPT}"}]}'
                        data-path="llm.genericRest.bodyTemplate" aria-label="REST body template"
                        style="width: 350px; font-family: var(--font-mono); font-size: 12px;">${escapeHtml(config.llm?.genericRest?.bodyTemplate || '')}</textarea>
                </div>
                <div class="config-field">
                    <label class="config-label" for="cfg-rest-path">
                        Response Path
                        <small>Dot-notation path to extract the text content from the JSON response body. Use array indexing with [N]. Example for OpenAI: choices[0].message.content</small>
                    </label>
                    <input id="cfg-rest-path" class="config-input" type="text"
                        value="${escapeAttr(config.llm?.genericRest?.responsePath || '')}"
                        placeholder="choices[0].message.content"
                        data-path="llm.genericRest.responsePath" aria-label="Response path">
                </div>
            </div>

            <div class="config-field" style="margin-top: 16px; border-top: 1px solid var(--border-color); padding-top: 16px;">
                <label class="config-label" for="cfg-system-prompt">
                    System Prompt
                    <small>Instructions passed to the LLM defining how it should evaluate safety. Sent via --system-prompt for CLI providers, or as the "system" message for API providers. The LLM is expected to return JSON with safetyScore, reasoning, and category fields.</small>
                </label>
                <textarea id="cfg-system-prompt" class="config-input" rows="4"
                    data-path="llm.systemPrompt" aria-label="System prompt"
                    style="width: 350px; font-family: var(--font-mono); font-size: 12px;">${escapeHtml(config.llm?.systemPrompt || '')}</textarea>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-prompt-prefixes">
                    Prompt Prefixes
                    <small>Messages sent to the persistent CLI before each safety analysis prompt (one per line). Each prefix is a separate conversation turn, adding latency. ACP providers create fresh sessions per query, so prefixes are rarely needed. Leave empty unless you need to prime the LLM.</small>
                </label>
                <textarea id="cfg-prompt-prefixes" class="config-input" rows="2"
                    data-path="llm.promptPrefixes" data-type="string-array" aria-label="Prompt prefixes"
                    placeholder=""
                    style="width: 350px; font-family: var(--font-mono); font-size: 12px;">${escapeHtml((config.llm?.promptPrefixes || []).join('\n'))}</textarea>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-prompt-suffixes">
                    Prompt Suffixes
                    <small>Messages sent to the persistent CLI after each safety analysis prompt (one per line). Each suffix is a separate conversation turn. Useful for follow-up instructions like "summarize your analysis".</small>
                </label>
                <textarea id="cfg-prompt-suffixes" class="config-input" rows="2"
                    data-path="llm.promptSuffixes" data-type="string-array" aria-label="Prompt suffixes"
                    style="width: 350px; font-family: var(--font-mono); font-size: 12px;">${escapeHtml((config.llm?.promptSuffixes || []).join('\n'))}</textarea>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-resolve-hook-symlinks">
                    Resolve Symlinks in Hook Paths
                    <small>When enabled, resolves symlinks in file paths and working directories before sending them to the LLM for safety analysis. This prevents false positives when your project directory is a symlink (e.g. C:\r\project → C:\Users\...\repos\project) — without this, the LLM sees mismatched paths and may flag safe operations as risky. Default: On.</small>
                </label>
                <select id="cfg-resolve-hook-symlinks" class="config-input"
                    data-path="resolveHookSymlinks" data-type="bool" aria-label="Resolve hook symlinks">
                    <option value="true" ${config.resolveHookSymlinks !== false ? 'selected' : ''}>On</option>
                    <option value="false" ${config.resolveHookSymlinks === false ? 'selected' : ''}>Off</option>
                </select>
            </div>
        </div>

        <div class="config-section">
            <h3>Enforcement</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-enforcement-mode">
                    Enforcement Mode
                    <small>Controls how Leash responds to hook events. Observe: logs events with no enforcement (LLM analysis optional). Approve-Only: auto-approves safe requests, shows interactive tray dialog for unsafe ones, never auto-denies. Enforce: auto-approves safe, auto-denies unsafe unless overridden via tray dialog.</small>
                </label>
                <select id="cfg-enforcement-mode" class="config-input"
                    data-path="enforcementMode" aria-label="Enforcement mode">
                    <option value="observe" ${(config.enforcementMode || 'observe') === 'observe' ? 'selected' : ''}>Observe (log only, no decisions)</option>
                    <option value="approve-only" ${config.enforcementMode === 'approve-only' ? 'selected' : ''}>Approve-Only (auto-approve safe, never deny)</option>
                    <option value="enforce" ${config.enforcementMode === 'enforce' ? 'selected' : ''}>Enforce (approve or deny based on analysis)</option>
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-analyze-observe">
                    Analyze in Observe Mode
                    <small>When enabled, Leash still runs LLM safety analysis in observe mode and shows scores in logs and tray notifications — but never sends approve/deny decisions. Useful for calibrating thresholds before enabling enforcement.</small>
                </label>
                <select id="cfg-analyze-observe" class="config-input"
                    data-path="analyzeInObserveMode" data-type="bool" aria-label="Analyze in observe mode">
                    <option value="true" ${config.analyzeInObserveMode !== false ? 'selected' : ''}>Yes</option>
                    <option value="false" ${config.analyzeInObserveMode === false ? 'selected' : ''}>No</option>
                </select>
            </div>
        </div>

        <div class="config-section">
            <h3>Copilot Integration</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-copilot-enabled">
                    Copilot Enabled
                    <small>Enable or disable hook processing for GitHub Copilot CLI events. When disabled, Leash ignores all incoming Copilot hook requests (returns empty {} for every event).</small>
                </label>
                <select id="cfg-copilot-enabled" class="config-input"
                    data-path="copilot.enabled" data-type="bool" aria-label="Copilot enabled">
                    <option value="true" ${config.copilot?.enabled !== false ? 'selected' : ''}>Enabled</option>
                    <option value="false" ${config.copilot?.enabled === false ? 'selected' : ''}>Disabled</option>
                </select>
            </div>
        </div>

        <div class="config-section">
            <h3>Security</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-security-apikey">
                    API Key
                    <small>When set, all API requests to Leash must include a matching X-Api-Key header. This protects the Leash API from unauthorized access on shared networks. Leave empty to allow unauthenticated access (suitable for localhost-only use).</small>
                </label>
                <input id="cfg-security-apikey" class="config-input" type="password"
                    value="${escapeAttr(config.security?.apiKey || '')}"
                    placeholder="(no API key required)"
                    data-path="security.apiKey" aria-label="Security API key"
                    autocomplete="off">
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-security-ratelimit">
                    Rate Limit (req/min)
                    <small>Maximum number of API requests allowed per minute per IP address. Prevents runaway hook loops or abuse. Claude Code typically sends 1–5 requests per user action. Default: 600.</small>
                </label>
                <input id="cfg-security-ratelimit" class="config-input" type="number"
                    value="${config.security?.rateLimitPerMinute || 600}" min="10" max="10000"
                    data-path="security.rateLimitPerMinute" aria-label="Rate limit per minute">
            </div>
        </div>

        <div class="config-section">
            <h3>Profiles</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-active-profile">
                    Active Profile
                    <small>The permission profile that controls default safety thresholds and handler behavior. Built-in profiles: "strict" (low thresholds, more denials), "moderate" (balanced), "permissive" (high thresholds, fewer denials). Custom profiles can be defined in the JSON config file.</small>
                </label>
                <input id="cfg-active-profile" class="config-input" type="text"
                    value="${escapeAttr(config.profiles?.activeProfile || 'moderate')}"
                    data-path="profiles.activeProfile" aria-label="Active profile">
            </div>
        </div>

        <div class="config-section">
            <h3>Session</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-maxhistory">
                    Max History Per Session
                    <small>Maximum number of hook events retained in memory per Claude/Copilot session. Older events are discarded when this limit is reached. Visible on the Live Logs page. Higher values use more memory. Default: 50.</small>
                </label>
                <input id="cfg-maxhistory" class="config-input" type="number"
                    value="${config.session?.maxHistoryPerSession || 50}" min="1" max="1000"
                    data-path="session.maxHistoryPerSession" aria-label="Max history per session">
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-storagedir">
                    Storage Directory
                    <small>Directory where Leash persists session data (event logs, analysis results). Supports ~ for home directory. Default: ~/.leash/sessions</small>
                </label>
                <input id="cfg-storagedir" class="config-input" type="text"
                    value="${escapeAttr(config.session?.storageDir || '')}"
                    data-path="session.storageDir" aria-label="Storage directory"
                    style="width: 300px;">
            </div>
        </div>

        <div class="config-section">
            <h3>System Tray &amp; Notifications</h3>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
                Native OS notifications for safety decisions. In observe mode: informational alerts only.
                In approve-only and enforce modes: interactive Approve/Deny dialogs for unsafe requests.
            </p>
            <div class="config-field">
                <label class="config-label" for="cfg-tray-enabled">
                    Tray Icon
                    <small>Master switch for the system tray icon and all desktop notifications. When disabled, no tray icon appears and no native OS alerts are shown. Enforcement still works — decisions are made automatically without user interaction.</small>
                </label>
                <select id="cfg-tray-enabled" class="config-input"
                    data-path="tray.enabled" data-type="bool" aria-label="Tray enabled">
                    <option value="true" ${config.tray?.enabled !== false ? 'selected' : ''}>Enabled</option>
                    <option value="false" ${config.tray?.enabled === false ? 'selected' : ''}>Disabled</option>
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-tray-showInObserve">
                    Notifications in Observe Mode
                    <small>Show informational tray alerts in observe mode (no approve/deny buttons, just scores). Only works when "Analyze in Observe Mode" is also enabled. Useful to preview what Leash would flag before enabling enforcement.</small>
                </label>
                <select id="cfg-tray-showInObserve" class="config-input"
                    data-path="tray.showInObserve" data-type="bool" aria-label="Show in observe">
                    <option value="false" ${config.tray?.showInObserve !== true ? 'selected' : ''}>Off</option>
                    <option value="true" ${config.tray?.showInObserve === true ? 'selected' : ''}>On</option>
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-tray-showInApproveOnly">
                    Notifications in Approve-Only Mode
                    <small>Show interactive approve/deny toast dialogs for requests with uncertain safety scores. When enabled, you can manually approve or deny borderline requests. When disabled, uncertain requests default to no-opinion (ask user).</small>
                </label>
                <select id="cfg-tray-showInApproveOnly" class="config-input"
                    data-path="tray.showInApproveOnly" data-type="bool" aria-label="Show in approve-only">
                    <option value="true" ${config.tray?.showInApproveOnly !== false ? 'selected' : ''}>On</option>
                    <option value="false" ${config.tray?.showInApproveOnly === false ? 'selected' : ''}>Off</option>
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-tray-interactiveTimeout">
                    Interactive Timeout (seconds)
                    <small>How long the approve/deny dialog stays open before timing out. In approve-only mode, timeout = no-opinion. In enforce mode, timeout = deny. Range: 5–30 seconds.</small>
                </label>
                <input id="cfg-tray-interactiveTimeout" class="config-input" type="number"
                    value="${config.tray?.interactiveTimeoutSeconds || 10}" min="5" max="30"
                    data-path="tray.interactiveTimeoutSeconds" aria-label="Interactive timeout">
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-tray-sound">
                    Notification Sound
                    <small>Play the default OS notification sound when tray alerts appear. Helpful when you're not watching the screen.</small>
                </label>
                <select id="cfg-tray-sound" class="config-input"
                    data-path="tray.sound" data-type="bool" aria-label="Notification sound">
                    <option value="false" ${config.tray?.sound !== true ? 'selected' : ''}>Off</option>
                    <option value="true" ${config.tray?.sound === true ? 'selected' : ''}>On</option>
                </select>
            </div>
            <div class="config-field">
                <label class="config-label" for="cfg-tray-useLargePopup">
                    Large Decision Popup
                    <small>Use a custom popup window with colored Approve/Deny buttons instead of the native OS toast notification. The popup is larger and more visible. Requires a server restart to take effect.</small>
                </label>
                <select id="cfg-tray-useLargePopup" class="config-input"
                    data-path="tray.useLargePopup" data-type="bool" aria-label="Use large popup">
                    <option value="true" ${config.tray?.useLargePopup !== false ? 'selected' : ''}>Enabled</option>
                    <option value="false" ${config.tray?.useLargePopup === false ? 'selected' : ''}>Disabled (use native toast)</option>
                </select>
            </div>
        </div>

        <div class="config-section">
            <h3>Transcripts</h3>
            <div class="config-field">
                <label class="config-label" for="cfg-resolve-symlinks">
                    Resolve Symlinks
                    <small>When browsing transcripts, resolve symlinked directory paths to their real locations (e.g. c:\\r → c:\\users\\...\\repos). Enable this if your project directories are symlinks and transcripts aren't being found.</small>
                </label>
                <select id="cfg-resolve-symlinks" class="config-input"
                    data-path="resolveSymlinks" data-type="bool" aria-label="Resolve symlinks">
                    <option value="false" ${config.resolveSymlinks !== true ? 'selected' : ''}>Off</option>
                    <option value="true" ${config.resolveSymlinks === true ? 'selected' : ''}>On</option>
                </select>
            </div>
        </div>

        <div class="config-section">
            <h3>Triggers (Webhooks)</h3>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
                Fire HTTP webhook requests when hook events occur. Useful for integrating with external monitoring, alerting, or logging systems. Configure individual trigger rules (URL, method, event filters) in the JSON config file at ~/.leash/config.json.
            </p>
            <div class="config-field">
                <label class="config-label" for="cfg-triggers-enabled">
                    Triggers Enabled
                    <small>Master switch for all webhook triggers. When disabled, no outbound HTTP requests are sent regardless of trigger rules.</small>
                </label>
                <select id="cfg-triggers-enabled" class="config-input"
                    data-path="triggers.enabled" data-type="bool" aria-label="Triggers enabled">
                    <option value="true" ${config.triggers?.enabled ? 'selected' : ''}>Enabled</option>
                    <option value="false" ${!config.triggers?.enabled ? 'selected' : ''}>Disabled</option>
                </select>
            </div>
        </div>

    `;

    // Add change listeners — auto-save on change with debounce
    container.querySelectorAll('.config-input').forEach(input => {
        const eventType = input.tagName === 'SELECT' ? 'change' : 'input';
        input.addEventListener(eventType, () => {
            isDirty = true;
            _scheduleConfigAutoSave();
        });
    });

    // Wire custom model input to also trigger per-provider model save
    const customModelInput = document.getElementById('cfg-model-custom');
    if (customModelInput) {
        customModelInput.addEventListener('input', () => {
            _saveProviderModel();
        });
    }

    // Show/hide provider-specific fields
    updateProviderFields();
}

function updateProviderFields() {
    const provider = document.getElementById('cfg-provider')?.value || 'anthropic-api';
    const persistentProviders = ['claude-persistent', 'claude-stream', 'copilot-persistent'];

    const apiFields = document.getElementById('provider-anthropic-api');
    const cliFields = document.getElementById('provider-cli');
    const restFields = document.getElementById('provider-generic-rest');
    const persistentFields = document.getElementById('provider-persistent');

    if (apiFields) apiFields.style.display = provider === 'anthropic-api' ? 'block' : 'none';
    if (cliFields) cliFields.style.display = ['claude-cli', 'claude-persistent', 'claude-stream', 'copilot-cli', 'copilot-persistent'].includes(provider) ? 'block' : 'none';
    if (restFields) restFields.style.display = provider === 'generic-rest' ? 'block' : 'none';
    if (persistentFields) persistentFields.style.display = persistentProviders.includes(provider) ? 'block' : 'none';

    // Update model dropdown and effort level for the selected provider
    _updateModelDropdown(provider);
    _updateEffortVisibility(provider);
}

async function _ensureProviderModelsData() {
    if (_providerModelsData) return _providerModelsData;
    try {
        _providerModelsData = await fetchApi('/api/config/provider-models');
    } catch (e) {
        // Don't cache failure — return fallback without storing so next call retries
        return { models: {}, effortLevels: {}, effortSupported: [] };
    }
    return _providerModelsData;
}

async function _updateModelDropdown(provider) {
    const select = document.getElementById('cfg-model');
    if (!select) return;

    const data = await _ensureProviderModelsData();

    // Guard against race condition: if user switched providers while we were
    // fetching, the provider arg is stale — bail out silently.
    const currentProvider = document.getElementById('cfg-provider')?.value || '';
    if (currentProvider !== provider) return;

    const models = (data.models && data.models[provider]) || [];

    // Determine current model: check per-provider override first, then global.
    // An explicit empty string override means "use provider default".
    const providerModels = currentConfig?.llm?.providerModels || {};
    const hasOverride = provider in providerModels;
    const currentModel = hasOverride ? providerModels[provider] : (currentConfig?.llm?.model ?? '');

    select.innerHTML = '';
    let foundCurrent = false;

    for (const m of models) {
        const opt = document.createElement('option');
        opt.value = m.value;
        opt.textContent = m.label;
        if (m.value === currentModel) {
            opt.selected = true;
            foundCurrent = true;
        }
        select.appendChild(opt);
    }

    // Add "Custom..." option
    const customOpt = document.createElement('option');
    customOpt.value = '__custom__';
    customOpt.textContent = 'Custom...';
    select.appendChild(customOpt);

    // If the current model isn't in the list, select Custom and pre-fill
    const customWrapper = document.getElementById('custom-model-wrapper');
    const customInput = document.getElementById('cfg-model-custom');
    if (!foundCurrent && currentModel && models.length > 0) {
        select.value = '__custom__';
        if (customWrapper) customWrapper.style.display = '';
        if (customInput) customInput.value = currentModel;
    } else {
        if (customWrapper) customWrapper.style.display = 'none';
        if (customInput) customInput.value = '';
    }
}

function onModelDropdownChange() {
    const select = document.getElementById('cfg-model');
    const customWrapper = document.getElementById('custom-model-wrapper');
    const customInput = document.getElementById('cfg-model-custom');
    if (!select) return;

    if (select.value === '__custom__') {
        if (customWrapper) customWrapper.style.display = '';
        if (customInput) customInput.focus();
    } else {
        if (customWrapper) customWrapper.style.display = 'none';
        if (customInput) customInput.value = '';
    }

    // Save the per-provider model override
    _saveProviderModel();
}

function _saveProviderModel() {
    const provider = document.getElementById('cfg-provider')?.value;
    const select = document.getElementById('cfg-model');
    const customInput = document.getElementById('cfg-model-custom');
    if (!provider || !select || !currentConfig) return;

    let modelValue = select.value;
    if (modelValue === '__custom__' && customInput) {
        modelValue = customInput.value || '';
    }

    // Update both global model and per-provider map
    if (!currentConfig.llm) currentConfig.llm = {};
    if (!currentConfig.llm.providerModels) currentConfig.llm.providerModels = {};
    currentConfig.llm.providerModels[provider] = modelValue;
    // Also set the global model to the active provider's model for backward compat
    currentConfig.llm.model = modelValue;
    isDirty = true;
    _scheduleConfigAutoSave();
}

async function _updateEffortVisibility(provider) {
    const effortField = document.getElementById('effort-field');
    if (!effortField) return;

    const data = await _ensureProviderModelsData();
    const supported = data.effortSupported || [];
    const isSupported = supported.includes(provider);
    effortField.style.display = isSupported ? '' : 'none';

    // Dynamically populate effort dropdown from server data
    if (isSupported) {
        const select = document.getElementById('cfg-effort');
        if (select && data.effortLevels) {
            const currentEffort = currentConfig?.llm?.effortLevel || '';
            select.innerHTML = '';
            const providerLevels = data.effortLevels?.[provider] || data.effortLevels?.['claude-cli'] || [];
            for (const level of providerLevels) {
                const opt = document.createElement('option');
                opt.value = level.value;
                opt.textContent = level.label;
                if (level.value === currentEffort) opt.selected = true;
                select.appendChild(opt);
            }
        }
    }
}

function _scheduleConfigAutoSave() {
    if (_configSaveTimer) clearTimeout(_configSaveTimer);
    _configSaveTimer = setTimeout(function() { saveConfig(); }, _AUTO_SAVE_DELAY);
}

function _scheduleHookHandlersAutoSave() {
    if (_hookHandlersSaveTimer) clearTimeout(_hookHandlersSaveTimer);
    _hookHandlersSaveTimer = setTimeout(function() { saveHookHandlers(); }, _AUTO_SAVE_DELAY);
}

function markHookHandlersDirty() {
    hookHandlersDirty = true;
    _scheduleHookHandlersAutoSave();
}

async function saveConfig() {
    if (!currentConfig || !isDirty) return;

    const inputs = document.querySelectorAll('.config-input');
    const updated = JSON.parse(JSON.stringify(currentConfig));

    inputs.forEach(input => {
        const path = input.dataset.path;
        if (!path) return;

        // Skip hidden provider fields to avoid overwriting config with empty values
        const providerSection = input.closest('.provider-fields');
        if (providerSection && providerSection.style.display === 'none') return;

        // Special handling for model dropdown — resolve __custom__ value
        if (input.id === 'cfg-model') {
            let modelVal = input.value;
            if (modelVal === '__custom__') {
                const customInput = document.getElementById('cfg-model-custom');
                modelVal = customInput ? customInput.value : '';
            }
            if (!updated.llm) updated.llm = {};
            updated.llm.model = modelVal;
            return;
        }
        // Skip the custom model text input (handled above via dropdown)
        if (input.id === 'cfg-model-custom') return;

        const parts = path.split('.');
        let obj = updated;
        for (let i = 0; i < parts.length - 1; i++) {
            if (!obj[parts[i]]) obj[parts[i]] = {};
            obj = obj[parts[i]];
        }

        const key = parts[parts.length - 1];
        if (input.dataset.type === 'bool') {
            obj[key] = input.value === 'true';
        } else if (input.dataset.type === 'string-array') {
            obj[key] = (input.value || '').split('\n').map(s => s.trim()).filter(s => s.length > 0);
        } else if (input.type === 'number') {
            obj[key] = parseInt(input.value, 10);
        } else if (key === 'headers' && input.tagName === 'TEXTAREA') {
            try {
                obj[key] = JSON.parse(input.value || '{}');
            } catch {
                obj[key] = {};
            }
        } else {
            let val = input.value;
            // Convert empty effort level to null (backend expects null for default)
            if (key === 'effortLevel' && !val) val = null;
            // Preserve empty string for model (means "use provider default")
            // but convert other empty strings to null
            if (key === 'model') {
                obj[key] = val;
            } else {
                obj[key] = val === '' ? null : val;
            }
        }
    });

    if (updated.enforcementMode) {
        updated.enforcementEnabled = updated.enforcementMode === 'enforce' || updated.enforcementMode === 'approve-only';
    }

    try {
        var response = await fetch('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updated)
        });

        if (!response.ok) {
            var errData = await response.json().catch(function() { return {}; });
            throw new Error(errData.error || ('Server returned ' + response.status));
        }

        currentConfig = updated;
        isDirty = false;
        Toast.show('Settings Updated', 'Configuration saved successfully.', 'success', 2000);
    } catch (error) {
        Toast.show('Save Failed', error.message, 'danger');
    }
}

/* ==========================================================================
   Hook Handlers UI
   ========================================================================== */

function getHookHandlers() {
    if (!currentConfig) return {};
    return currentConfig.hookHandlers || {};
}

function ensureHookEventConfig(eventType) {
    if (!currentConfig.hookHandlers) {
        currentConfig.hookHandlers = {};
    }
    if (!currentConfig.hookHandlers[eventType]) {
        currentConfig.hookHandlers[eventType] = { enabled: true, handlers: [] };
    }
    return currentConfig.hookHandlers[eventType];
}

function getAllHookEventTypes() {
    // Merge predefined types with any custom ones from config
    const fromConfig = Object.keys(getHookHandlers());
    const all = new Set(HOOK_EVENT_TYPES);
    fromConfig.forEach(function(k) { all.add(k); });
    return Array.from(all);
}

function addHookEventType() {
    const input = document.getElementById('newHookEventInput');
    if (!input) return;
    const name = input.value.trim();
    if (!name) return;
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) {
        Toast.show('Invalid Name', 'Hook event name must be alphanumeric (e.g. PreToolUse)', 'warning');
        return;
    }
    const hookHandlers = getHookHandlers();
    if (hookHandlers[name]) {
        Toast.show('Already Exists', 'Hook event "' + name + '" already exists', 'warning');
        return;
    }
    ensureHookEventConfig(name);
    markHookHandlersDirty();
    renderHookHandlers();
    input.value = '';
}

function removeHookEventType(eventType) {
    if (!currentConfig || !currentConfig.hookHandlers) return;
    delete currentConfig.hookHandlers[eventType];
    markHookHandlersDirty();
    renderHookHandlers();
}

function renderHookHandlers() {
    const container = document.getElementById('hookHandlersContent');
    if (!container || !currentConfig) return;

    const hookHandlers = getHookHandlers();
    const allEventTypes = getAllHookEventTypes();

    let html = '<div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;">' +
        '<input type="text" id="newHookEventInput" placeholder="New hook event name (e.g. PermissionRequest)" ' +
        'style="flex:1;padding:4px 8px;font-size:0.85em;border:1px solid var(--border-color,#ddd);border-radius:4px;background:var(--bg-secondary,#fff);color:var(--text-primary,#1a1a2e);">' +
        '<button class="btn btn-sm" onclick="addHookEventType()" style="font-size:11px;white-space:nowrap;">+ Add Hook Event</button>' +
        '</div>';

    for (const eventType of allEventTypes) {
        const eventConfig = hookHandlers[eventType] || { enabled: true, handlers: [] };
        const handlers = eventConfig.handlers || [];
        const enabled = eventConfig.enabled !== false;
        const description = HOOK_EVENT_DESCRIPTIONS[eventType] || '';
        const isCustom = !HOOK_EVENT_TYPES.includes(eventType);

        html += `
        <div class="hook-event-card" data-event="${escapeAttr(eventType)}">
            <div class="hook-event-header">
                <div class="hook-event-title">
                    <h4>${escapeHtml(eventType)} ${getHarnessIcons(eventType)}${isCustom ? ' <span style="font-size:0.65em;font-weight:400;color:var(--text-muted);">(custom)</span>' : ''}</h4>
                    <label class="hook-event-toggle">
                        <input type="checkbox" ${enabled ? 'checked' : ''}
                            onchange="toggleHookEvent('${escapeAttr(eventType)}', this.checked)"
                            style="cursor: pointer;">
                        Enabled
                    </label>
                </div>
                <div style="display:flex;gap:4px;">
                    <button class="btn btn-sm" onclick="addHandler('${escapeAttr(eventType)}')" style="font-size: 11px;">+ Add Handler</button>
                    <button class="btn btn-sm" onclick="removeHookEventType('${escapeAttr(eventType)}')" style="font-size: 11px; color: var(--color-danger); border-color: var(--color-danger);" title="Remove this hook event">Remove</button>
                </div>
            </div>
            ${description ? `<p class="hook-event-desc">${escapeHtml(description)}</p>` : ''}
            <div id="handlers-${escapeAttr(eventType)}">
                ${handlers.length === 0
                    ? '<p class="hook-empty-msg">No handlers configured. Click "+ Add Handler" to create one.</p>'
                    : handlers.map((h, idx) => renderHandlerRow(eventType, h, idx, false)).join('')
                }
            </div>
        </div>`;
    }

    container.innerHTML = html;
}

function toggleHandlerEnabled(eventType, index) {
    const eventConfig = (currentConfig.hookHandlers || {})[eventType];
    if (!eventConfig || !eventConfig.handlers || !eventConfig.handlers[index]) return;
    eventConfig.handlers[index].enabled = !eventConfig.handlers[index].enabled;
    markHookHandlersDirty();
    renderEventHandlers(eventType);
}

function renderHandlerRow(eventType, handler, index, editing) {
    if (editing) {
        return renderHandlerEditRow(eventType, handler, index);
    }

    const safeEvent = escapeAttr(eventType);
    const rowId = `handler-${safeEvent}-${index}`;
    const badgeColor = MODE_BADGE_COLORS[handler.mode] || 'var(--text-faint)';
    const promptFile = handler.promptTemplate ? handler.promptTemplate.replace(/^.*[\\\/]/, '') : '';
    const isDisabled = handler.enabled === false;
    const disabledStyle = isDisabled ? 'opacity: 0.45;' : '';
    const disabledBadge = isDisabled ? '<span style="font-size:0.7em;font-weight:600;color:var(--text-muted);background:var(--bg-tertiary,#e5e7eb);padding:1px 5px;border-radius:3px;margin-left:4px;">DISABLED</span>' : '';

    return `
    <div id="${rowId}" class="handler-row" style="${disabledStyle}">
        <div class="handler-row-details">
            <label style="cursor:pointer;display:inline-flex;align-items:center;margin-right:6px;" title="${isDisabled ? 'Enable' : 'Disable'} this handler">
                <input type="checkbox" ${isDisabled ? '' : 'checked'} onchange="toggleHandlerEnabled('${safeEvent}', ${index})" onclick="event.stopPropagation()" style="cursor:pointer;">
            </label>
            <span class="handler-name" title="Handler name">${escapeHtml(handler.name || '(unnamed)')}</span>${disabledBadge}
            <span class="handler-mode-badge" style="background: ${badgeColor};" title="Mode">${escapeHtml(handler.mode || 'log-only')}</span>
            <span class="handler-client-badge" title="Client: ${escapeAttr(handler.client || 'all')}">${handler.client ? escapeHtml(handler.client) : 'all'}</span>
            <code class="handler-matcher" title="Matcher pattern: ${escapeAttr(handler.matcher || '*')}">${escapeHtml(handler.matcher || '*')}</code>
            <span class="handler-thresholds" title="Thresholds: Strict / Moderate / Permissive / Trust">S:<strong>${handler.thresholdStrict || 95}</strong> M:<strong>${handler.thresholdModerate || 85}</strong> P:<strong>${handler.thresholdPermissive || 70}</strong> T:<strong>${handler.thresholdTrust || 50}</strong></span>
            ${handler.autoApprove ? '<span class="handler-auto-approve" title="Auto-approve enabled">Auto-approve</span>' : ''}
            ${promptFile ? `<span class="handler-prompt-label" title="Prompt: ${escapeAttr(handler.promptTemplate)}">Prompt: ${escapeHtml(promptFile)}</span>` : ''}
        </div>
        <div class="handler-actions">
            <button class="btn btn-sm" onclick="editHandler('${safeEvent}', ${index})" style="font-size: 11px; padding: 2px 8px;">Edit</button>
            <button class="btn btn-sm" onclick="removeHandler('${safeEvent}', ${index})" style="font-size: 11px; padding: 2px 8px; color: var(--color-danger); border-color: var(--color-danger);">Remove</button>
        </div>
    </div>`;
}

function renderHandlerEditRow(eventType, handler, index) {
    const safeEvent = escapeAttr(eventType);
    const rowId = `handler-${safeEvent}-${index}`;

    // Match by filename - handler.promptTemplate may be a full path
    const currentPromptFile = handler.promptTemplate ? handler.promptTemplate.replace(/^.*[\\\/]/, '') : '';
    const promptOptions = promptTemplateNames.map(name =>
        `<option value="${escapeAttr(name)}" ${currentPromptFile === name ? 'selected' : ''}>${escapeHtml(name)}</option>`
    ).join('');

    const modeOptions = HANDLER_MODES.map(m =>
        `<option value="${escapeAttr(m.value)}" ${handler.mode === m.value ? 'selected' : ''}>${escapeHtml(m.label)}</option>`
    ).join('');

    return `
    <div id="${rowId}" class="handler-edit-card">
        <div class="handler-edit-grid">
            <div>
                <label class="handler-edit-label">Name</label>
                <input type="text" id="${rowId}-name" value="${escapeAttr(handler.name || '')}"
                    placeholder="e.g. bash-analyzer"
                    class="handler-edit-input handler-edit-input-mono">
            </div>
            <div>
                <label class="handler-edit-label">Matcher Pattern (regex)</label>
                <input type="text" id="${rowId}-matcher" value="${escapeAttr(handler.matcher || '')}"
                    placeholder="e.g. Bash|Write or *"
                    class="handler-edit-input handler-edit-input-mono">
            </div>
            <div>
                <label class="handler-edit-label">Client</label>
                <select id="${rowId}-client" class="handler-edit-input">
                    <option value="" ${!handler.client ? 'selected' : ''}>All Clients</option>
                    <option value="claude" ${handler.client === 'claude' ? 'selected' : ''}>Claude</option>
                    <option value="copilot" ${handler.client === 'copilot' ? 'selected' : ''}>Copilot</option>
                </select>
            </div>
            <div>
                <label class="handler-edit-label">Mode</label>
                <select id="${rowId}-mode" class="handler-edit-input">
                    ${modeOptions}
                </select>
            </div>
            <div>
                <label class="handler-edit-label">Prompt Template</label>
                <select id="${rowId}-prompt" class="handler-edit-input">
                    <option value="">(none)</option>
                    ${promptOptions}
                </select>
            </div>
            <div>
                <label class="handler-edit-label">Strict Threshold</label>
                <input type="number" id="${rowId}-thresholdStrict" value="${handler.thresholdStrict != null ? handler.thresholdStrict : 95}"
                    min="0" max="100" class="handler-edit-input handler-edit-input-mono">
            </div>
            <div>
                <label class="handler-edit-label">Moderate Threshold</label>
                <input type="number" id="${rowId}-thresholdModerate" value="${handler.thresholdModerate != null ? handler.thresholdModerate : 85}"
                    min="0" max="100" class="handler-edit-input handler-edit-input-mono">
            </div>
            <div>
                <label class="handler-edit-label">Permissive Threshold</label>
                <input type="number" id="${rowId}-thresholdPermissive" value="${handler.thresholdPermissive != null ? handler.thresholdPermissive : 70}"
                    min="0" max="100" class="handler-edit-input handler-edit-input-mono">
            </div>
            <div>
                <label class="handler-edit-label">Trust Threshold</label>
                <input type="number" id="${rowId}-thresholdTrust" value="${handler.thresholdTrust != null ? handler.thresholdTrust : 50}"
                    min="0" max="100" class="handler-edit-input handler-edit-input-mono">
            </div>
            <div class="handler-edit-checkbox">
                <label>
                    <input type="checkbox" id="${rowId}-enabled" ${handler.enabled !== false ? 'checked' : ''}
                        style="cursor: pointer;">
                    Enabled
                </label>
            </div>
            <div class="handler-edit-checkbox">
                <label>
                    <input type="checkbox" id="${rowId}-autoapprove" ${handler.autoApprove ? 'checked' : ''}
                        style="cursor: pointer;">
                    Auto-approve when safe
                </label>
            </div>
        </div>
        <div class="handler-edit-actions">
            <button class="btn btn-sm" onclick="cancelEditHandler('${safeEvent}', ${index})" style="font-size: 11px; padding: 3px 10px;">Cancel</button>
            <button class="btn btn-sm btn-primary" onclick="applyEditHandler('${safeEvent}', ${index})" style="font-size: 11px; padding: 3px 10px;">Apply</button>
        </div>
    </div>`;
}

function toggleHookEvent(eventType, enabled) {
    const eventConfig = ensureHookEventConfig(eventType);
    eventConfig.enabled = enabled;
    markHookHandlersDirty();
}

function addHandler(eventType) {
    const eventConfig = ensureHookEventConfig(eventType);
    const newHandler = {
        name: '',
        enabled: true,
        matcher: '*',
        client: null,
        mode: 'log-only',
        promptTemplate: '',
        threshold: 85,
        autoApprove: false
    };
    eventConfig.handlers.push(newHandler);
    markHookHandlersDirty();

    // Re-render the handlers list for this event, with the new one in edit mode
    const handlersContainer = document.getElementById(`handlers-${eventType}`);
    if (handlersContainer) {
        const handlers = eventConfig.handlers;
        handlersContainer.innerHTML = handlers.map((h, idx) => {
            if (idx === handlers.length - 1) {
                return renderHandlerRow(eventType, h, idx, true);
            }
            return renderHandlerRow(eventType, h, idx, false);
        }).join('');
    }
}

function removeHandler(eventType, index) {
    const eventConfig = ensureHookEventConfig(eventType);
    if (index >= 0 && index < eventConfig.handlers.length) {
        const name = eventConfig.handlers[index].name || '(unnamed)';
        if (!confirm(`Remove handler "${name}" from ${eventType}?`)) return;
        eventConfig.handlers.splice(index, 1);
        markHookHandlersDirty();
        renderEventHandlers(eventType);
    }
}

function editHandler(eventType, index) {
    const eventConfig = ensureHookEventConfig(eventType);
    const handler = eventConfig.handlers[index];
    if (!handler) return;

    const handlersContainer = document.getElementById(`handlers-${eventType}`);
    if (handlersContainer) {
        const handlers = eventConfig.handlers;
        handlersContainer.innerHTML = handlers.map((h, idx) => {
            return renderHandlerRow(eventType, h, idx, idx === index);
        }).join('');
    }
}

function cancelEditHandler(eventType, index) {
    renderEventHandlers(eventType);
}

function applyEditHandler(eventType, index) {
    const safeEvent = escapeAttr(eventType);
    const rowId = `handler-${safeEvent}-${index}`;

    const nameEl = document.getElementById(`${rowId}-name`);
    const matcherEl = document.getElementById(`${rowId}-matcher`);
    const clientEl = document.getElementById(`${rowId}-client`);
    const modeEl = document.getElementById(`${rowId}-mode`);
    const promptEl = document.getElementById(`${rowId}-prompt`);
    const thresholdStrictEl = document.getElementById(`${rowId}-thresholdStrict`);
    const thresholdModerateEl = document.getElementById(`${rowId}-thresholdModerate`);
    const thresholdPermissiveEl = document.getElementById(`${rowId}-thresholdPermissive`);
    const thresholdTrustEl = document.getElementById(`${rowId}-thresholdTrust`);
    const enabledEl = document.getElementById(`${rowId}-enabled`);
    const autoApproveEl = document.getElementById(`${rowId}-autoapprove`);

    if (!nameEl) return;

    const eventConfig = ensureHookEventConfig(eventType);
    const handler = eventConfig.handlers[index];
    if (!handler) return;

    handler.name = nameEl.value.trim();
    handler.enabled = enabledEl ? enabledEl.checked : true;
    handler.matcher = matcherEl.value.trim() || '*';
    handler.client = clientEl.value || null;
    handler.mode = modeEl.value;
    handler.promptTemplate = promptEl.value || null;
    handler.thresholdStrict = parseInt(thresholdStrictEl?.value, 10) || 95;
    handler.thresholdModerate = parseInt(thresholdModerateEl?.value, 10) || 85;
    handler.thresholdPermissive = parseInt(thresholdPermissiveEl?.value, 10) || 70;
    handler.thresholdTrust = parseInt(thresholdTrustEl?.value, 10) || 50;
    handler.threshold = handler.thresholdModerate; // Default threshold = moderate
    handler.autoApprove = autoApproveEl.checked;

    markHookHandlersDirty();
    renderEventHandlers(eventType);
}

function renderEventHandlers(eventType) {
    const handlersContainer = document.getElementById(`handlers-${eventType}`);
    if (!handlersContainer) return;

    const eventConfig = (currentConfig.hookHandlers || {})[eventType] || { enabled: true, handlers: [] };
    const handlers = eventConfig.handlers || [];

    if (handlers.length === 0) {
        handlersContainer.innerHTML = '<p class="hook-empty-msg">No handlers configured. Click "+ Add Handler" to create one.</p>';
    } else {
        handlersContainer.innerHTML = handlers.map((h, idx) => renderHandlerRow(eventType, h, idx, false)).join('');
    }
}

async function saveHookHandlers() {
    if (!currentConfig || !hookHandlersDirty) return;

    try {
        var updated = JSON.parse(JSON.stringify(currentConfig));

        if (updated.hookHandlers) {
            for (var eventType of Object.keys(updated.hookHandlers)) {
                var ec = updated.hookHandlers[eventType];
                if (ec && ec.handlers) {
                    ec.handlers.forEach(function(h) {
                        if (!h.promptTemplate) delete h.promptTemplate;
                        if (h.config && Object.keys(h.config).length === 0) delete h.config;
                    });
                }
            }
        }

        await fetch('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updated)
        });

        currentConfig = updated;
        hookHandlersDirty = false;

        // Auto-sync hooks
        try {
            var hooksStatus = await (await fetch('/api/hooks/status')).json();
            if (hooksStatus.installed) {
                await fetch('/api/hooks/install', { method: 'POST' });
            }
        } catch { /* non-fatal */ }

        Toast.show('Handlers Updated', 'Hook handler configuration saved.', 'success', 2000);
    } catch (error) {
        Toast.show('Save Failed', error.message, 'danger');
    }
}

/* ==========================================================================
   Utilities
   ========================================================================== */

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

document.addEventListener('DOMContentLoaded', async function() {
    await loadConfig();

    // Deep-link: scroll to handler section if ?handler= param is present
    var handlerParam = new URLSearchParams(window.location.search).get('handler');
    if (handlerParam) {
        try {
            // Find the hook-event-card for this event type and scroll to it
            var escapedParam = typeof CSS !== 'undefined' && CSS.escape
                ? CSS.escape(handlerParam)
                : handlerParam.replace(/([^\w-])/g, '\\$1');
            var card = document.querySelector('.hook-event-card[data-event="' + escapedParam + '"]');
            if (card) {
                card.scrollIntoView({ behavior: 'smooth', block: 'start' });
                card.style.outline = '2px solid var(--accent-primary, #2563eb)';
                card.style.outlineOffset = '2px';
                setTimeout(function() { card.style.outline = ''; card.style.outlineOffset = ''; }, 3000);
            } else {
                Toast.show('Handler Not Found', 'The handler "' + handlerParam + '" was not found on this page.', 'warning');
            }
        } catch (e) {
            console.debug('Handler deep-link failed:', e);
            Toast.show('Handler Not Found', 'Could not locate handler section.', 'warning');
        }
    }
});
