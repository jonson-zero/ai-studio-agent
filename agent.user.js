// ==UserScript==
// @name         AI Studio Agent
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Zero-API-cost Local Coding Agent
// @updateURL    https://raw.githubusercontent.com/jonson-zero/ai-studio-agent/main/agent.user.js
// @downloadURL  https://raw.githubusercontent.com/jonson-zero/ai-studio-agent/main/agent.user.js
// @match        https://aistudio.google.com/*
// @noframes
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_setClipboard
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function() {
    'use strict';

    if (window.top !== window) {
        return;
    }

    const SERVICE_NAME = "ai-studio-agent";
    const SCRIPT_VERSION = "1.0";
    const MAX_AUTO_INSPECT = 4;
    const MAX_PATCH_RETRIES = 2;
    let patchRetryCount = 0;
    let needsUserscriptUpdate = false;
    let bridgeRequiredScriptVer = "1.0";
    let hasWarnedScriptOutdated = false;

    let currentPort = GM_getValue('bridge_port', 9099);
    let isServerOnline = false;
    let failedAttempts = 0;
    let isCollapsed = GM_getValue('toolbar_collapsed', false);

    let isAgentActive = GM_getValue('agent_active', false);
    let isAutoApply = GM_getValue('auto_apply', false);
    let activePresetInfo = { id: 'default', name: 'Default' };
    let availablePresets = {};
    let lastSyncedPresetId = GM_getValue('last_synced_preset_id', '');
    let currentSessionSyncedKey = '';
    let lastObservedUrl = window.location.href;
    let isSyncing = false;
    let syncDebounceTimer = null;

    function getSessionKey() {
        const cleanPath = (window.location.pathname || '') + (window.location.search || '');
        return `${cleanPath}::${activePresetInfo.id || 'default'}`;
    }

    let lastAppliedSignature = GM_getValue('last_applied_signature', null);
    let lastAutoProcessedSignature = null;
    let lastProcessedChangesSig = null;
    let lastHandledErrorSig = null;
    let appliedTransactions = {};
    let appliedSignatures = new Set();
    let transactionStates = {};

    function getChangesSignature(changes) {
        if (!changes || !Array.isArray(changes) || changes.length === 0) return '';
        return changes.map(c => `${(c.action||'').toLowerCase()}:${c.file_path||''}:${c.anchor_start||''}:${c.anchor_end||''}:${c.search||''}:${c.content||''}`).join('||');
    }
    let autoInspectStreak = 0;
    let wasGenerating = false;
    let lastHandledInspectSig = null;
    let isQueued = false;
    let isOperationInProgress = false;
    let currentToastTimer = null;

    let lastModelTextLength = 0;
    let textStableCounter = 0;
    let sessionHasGenerated = false;

    function getServerUrl() {
        return `http://127.0.0.1:${currentPort}`;
    }

    function parseSemver(str) {
        const cleaned = (str || '').replace(/^[vV]/, '').trim();
        return cleaned.split('.').map(x => parseInt(x, 10) || 0);
    }

    function isNewerVersion(remote, local) {
        const r = parseSemver(remote);
        const l = parseSemver(local);
        for (let i = 0; i < Math.max(r.length, l.length); i++) {
            const rv = r[i] || 0;
            const lv = l[i] || 0;
            if (rv > lv) return true;
            if (rv < lv) return false;
        }
        return false;
    }

    function safeClear(element) {
        if (!element) return;
        if (typeof element.replaceChildren === 'function') {
            element.replaceChildren();
        } else {
            while (element.firstChild) {
                element.removeChild(element.firstChild);
            }
        }
    }

    function createEl(tag, styles = {}, text = '') {
        const el = document.createElement(tag);
        Object.assign(el.style, styles);
        if (text) el.textContent = text;
        return el;
    }

    function showToast(msg, isError = false) {
        let toast = document.getElementById('studio-agent-toast');
        if (currentToastTimer) clearTimeout(currentToastTimer);

        if (!toast) {
            toast = createEl('div', {
                position: 'fixed',
                bottom: '68px',
                right: '28px',
                zIndex: '2147483647',
                padding: '8px 14px',
                borderRadius: '6px',
                fontSize: '12px',
                fontWeight: '600',
                boxShadow: '0 8px 24px rgba(0,0,0,0.6)',
                pointerEvents: 'none',
                maxWidth: '460px',
                lineHeight: '1.4',
                wordBreak: 'break-word',
                transition: 'opacity 0.15s ease, transform 0.15s ease'
            });
            toast.id = 'studio-agent-toast';
            document.body.appendChild(toast);
        }

        toast.style.background = isError ? '#d93025' : '#161b22';
        toast.style.color = isError ? '#fff' : '#3fb950';
        toast.style.border = isError ? '1px solid #ff4d4f' : '1px solid #30363d';
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
        toast.textContent = msg;

        currentToastTimer = setTimeout(() => {
            if (toast) {
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(4px)';
                setTimeout(() => { if (toast && toast.parentNode) toast.remove(); }, 200);
            }
        }, 3000);
    }

    function setNativeValue(element, value) {
        const valueSetter = Object.getOwnPropertyDescriptor(element, 'value')?.set;
        const prototype = Object.getPrototypeOf(element);
        const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
        if (prototypeValueSetter && valueSetter !== prototypeValueSetter) {
            prototypeValueSetter.call(element, value);
        } else if (valueSetter) {
            valueSetter.call(element, value);
        } else {
            element.value = value;
        }
        element.dispatchEvent(new Event('input', { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function getPromptInputArea() {
        const selectors = [
            'ms-chunk-input textarea',
            'ms-chunk-input div[contenteditable="true"]',
            'footer textarea',
            'footer div[contenteditable="true"]',
            '.input-area textarea',
            '.input-area div[contenteditable="true"]',
            'textarea[placeholder*="Type something"]',
            'textarea[placeholder*="prompt" i]',
            'div[contenteditable="true"][role="textbox"]',
            'ms-text-box textarea',
            'ms-text-box div[contenteditable="true"]'
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null && !el.closest('ms-system-instructions') && !el.closest('.monaco-editor')) {
                return el;
            }
        }
        const all = Array.from(document.querySelectorAll('textarea, div[contenteditable="true"]'));
        for (let i = all.length - 1; i >= 0; i--) {
            const el = all[i];
            if (el.offsetParent !== null && !el.closest('ms-system-instructions') && !el.closest('.monaco-editor')) {
                return el;
            }
        }
        return document.querySelector('textarea');
    }

    function findRunButton() {
        const msRunBtn = document.querySelector('ms-run-button button, ms-run-button, button.run-button, [data-test-id="run-button"]');
        if (msRunBtn && msRunBtn.offsetParent !== null) {
            const btn = msRunBtn.tagName === 'BUTTON' ? msRunBtn : msRunBtn.querySelector('button');
            if (btn && !btn.disabled) return btn;
            if (msRunBtn && !msRunBtn.disabled) return msRunBtn;
        }

        const container = document.querySelector('footer, .input-area, ms-chunk-input');
        if (container) {
            const b = container.querySelector('button[aria-label*="run" i], button[aria-label*="send" i], button.run-button');
            if (b && b.offsetParent !== null && !b.disabled) return b;
        }

        const buttons = Array.from(document.querySelectorAll('button'));
        return buttons.find(b => {
            if (b.offsetParent === null || b.disabled) return false;
            const txt = (b.innerText || '').trim().toLowerCase();
            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
            return (txt.startsWith('run') || aria.startsWith('run') || testId === 'run-button' ||
                   Boolean(b.querySelector('mat-icon[fonticon="play_arrow"], mat-icon[fonticon="send"]'))) &&
                   !txt.includes('settings') && !aria.includes('settings');
        }) || null;
    }

    function findStopButton() {
        const buttons = Array.from(document.querySelectorAll('ms-run-button button, ms-run-button, button.run-button, [data-test-id="stop-button"], footer button, .input-area button'));
        return buttons.find(b => {
            if (b.offsetParent === null || b.closest('#studio-agent-toolbar, #studio-file-modal')) return false;
            const txt = (b.innerText || '').trim().toLowerCase();
            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
            const isStopText = (txt === 'stop' || aria === 'stop' || aria.includes('stop generation') || testId.includes('stop'));
            const hasStopIcon = Boolean(b.querySelector('mat-icon[fonticon="stop"], mat-icon[data-mat-icon-name="stop"]'));
            return (isStopText || hasStopIcon) && !txt.includes('stop sequence');
        }) || null;
    }

    function detectRepetitionLoop(text) {
        if (!text || text.length < 120) return false;
        const tail = text.slice(-450);
        const loopRegex = /(.{10,80}?)\1{3,}/s;
        return loopRegex.test(tail);
    }

    async function insertTextToPrompt(text) {
        let inputArea = getPromptInputArea();
        if (!inputArea) {
            const start = Date.now();
            while (Date.now() - start < 2000) {
                await new Promise(r => setTimeout(r, 100));
                inputArea = getPromptInputArea();
                if (inputArea) break;
            }
        }

        if (!inputArea) {
            GM_setClipboard(text);
            showToast('Copied to clipboard (Paste with Ctrl+V)');
            return false;
        }

        inputArea.focus();
        await new Promise(r => setTimeout(r, 100));

        const isToolResult = text.startsWith('[TOOL RESULT:');
        const isTextarea = inputArea.tagName === 'TEXTAREA';
        if (isTextarea) {
            const nextVal = isToolResult ? text : (inputArea.value ? inputArea.value + '\n\n' + text : text);
            setNativeValue(inputArea, nextVal);
            inputArea.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            inputArea.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            try {
                inputArea.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: nextVal }));
            } catch (e) {}
        } else if (inputArea.getAttribute('contenteditable') === 'true') {
            if (isToolResult) {
                document.execCommand('selectAll', false, null);
                if (!document.execCommand('insertText', false, text)) {
                    inputArea.textContent = text;
                }
            } else {
                let success = false;
                try {
                    success = document.execCommand('insertText', false, '\n\n' + text);
                } catch (e) {}
                if (!success) {
                    inputArea.textContent = (inputArea.textContent ? inputArea.textContent + '\n\n' : '') + text;
                }
            }
            inputArea.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            inputArea.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            try {
                inputArea.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text }));
            } catch (e) {}
        }

        const verifyStart = Date.now();
        while (Date.now() - verifyStart < 1500) {
            const currentVal = isTextarea ? (inputArea.value || '') : (inputArea.textContent || '');
            if (currentVal && currentVal.includes(text.slice(0, 30))) {
                break;
            }
            await new Promise(r => setTimeout(r, 80));
        }

        await new Promise(r => setTimeout(r, 180));
        return true;
    }

    function triggerRunPrompt() {
        const inputArea = getPromptInputArea();
        if (inputArea) {
            inputArea.focus();
            const enterEvt = new KeyboardEvent('keydown', {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                ctrlKey: true
            });
            inputArea.dispatchEvent(enterEvt);
        }

        setTimeout(() => {
            if (isAiGenerating()) return;
            const runBtn = findRunButton();
            if (runBtn && !runBtn.disabled) runBtn.click();
        }, 250);
    }

    async function submitPrompt(text) {
        if (!text || !text.trim()) return false;

        const maxGenWait = 6000;
        const genStart = Date.now();
        while (isAiGenerating() && (Date.now() - genStart < maxGenWait)) {
            await new Promise(r => setTimeout(r, 150));
        }
        await new Promise(r => setTimeout(r, 300));

        const inserted = await insertTextToPrompt(text);
        if (!inserted) {
            showToast('Failed to insert prompt text', true);
            return false;
        }

        const maxBtnWait = 3500;
        const btnStart = Date.now();
        let runBtn = findRunButton();
        while ((!runBtn || runBtn.disabled) && (Date.now() - btnStart < maxBtnWait)) {
            await new Promise(r => setTimeout(r, 120));
            runBtn = findRunButton();
        }

        const inputArea = getPromptInputArea();
        if (inputArea) {
            const currentVal = (inputArea.tagName === 'TEXTAREA' ? inputArea.value : inputArea.textContent) || '';
            if (!currentVal.trim() || !currentVal.includes(text.slice(0, 30))) {
                await insertTextToPrompt(text);
                await new Promise(r => setTimeout(r, 200));
            }
            inputArea.focus();
        }

        runBtn = findRunButton();
        if (runBtn && !runBtn.disabled) {
            runBtn.click();
        } else if (inputArea) {
            const enterEvt = new KeyboardEvent('keydown', {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                ctrlKey: true
            });
            inputArea.dispatchEvent(enterEvt);
        }

        return true;
    }

    function repairAndSanitizeJson(raw) {
        if (!raw) return '';
        let s = raw.trim();

        // 0. Clean broken unicode sequences and control characters
        s = s.replace(/\\u(?![0-9a-fA-F]{4})/g, 'u');
        s = s.replace(/[\uFFFD\u0000-\u0008\u000B-\u000C\u000E-\u001F]/g, '');

        // 1. Strip markdown fences if present
        s = s.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');

        // 2. State-machine based character scanner to repair escape sequences, control chars & unescaped quotes
        let result = '';
        let inString = false;
        const len = s.length;

        for (let i = 0; i < len; i++) {
            const ch = s[i];

            if (inString) {
                if (ch === '\\') {
                    const next = i + 1 < len ? s[i + 1] : '';
                    if (/["\\\/bfnrt]/.test(next)) {
                        result += '\\' + next;
                        i++;
                        continue;
                    } else if (next === 'u' && /^[0-9a-fA-F]{4}/.test(s.slice(i + 2, i + 6))) {
                        result += '\\' + s.slice(i + 1, i + 6);
                        i += 5;
                        continue;
                    } else {
                        result += next;
                        i++;
                        continue;
                    }
                }

                if (ch === '"') {
                    const rest = s.slice(i + 1, i + 40).trim();
                    const isJsonDelim = /^([}\]]\s*[,}\]]?|,\s*["{\]]|:\s*["{\[])/.test(rest) || rest === '';
                    if (!isJsonDelim) {
                        result += '\\"';
                        continue;
                    }
                    inString = false;
                    result += '"';
                    continue;
                }

                if (ch === '\n') {
                    result += '\\n';
                    continue;
                }
                if (ch === '\r') {
                    continue;
                }
                if (ch === '\t') {
                    result += '\\t';
                    continue;
                }

                result += ch;
            } else {
                if (ch === '"') {
                    inString = true;
                    result += '"';
                    continue;
                }
                result += ch;
            }
        }

        // 3. Remove trailing commas before closing braces or brackets
        result = result.replace(/,\s*([}\]])/g, '$1');
        return result;
    }

    function extractBalancedJsonObject(str, startIndex) {
        let depth = 0;
        let inString = false;
        let escaped = false;

        for (let i = startIndex; i < str.length; i++) {
            const ch = str[i];
            if (inString) {
                if (escaped) {
                    escaped = false;
                } else if (ch === '\\') {
                    escaped = true;
                } else if (ch === '"') {
                    inString = false;
                }
                continue;
            }

            if (ch === '"') {
                inString = true;
                continue;
            }

            if (ch === '{') {
                depth++;
            } else if (ch === '}') {
                depth--;
                if (depth === 0) {
                    return str.substring(startIndex, i + 1);
                }
            }
        }
        return null;
    }

    function getTurnRawText(turn) {
        if (!turn) return '';
        const clone = turn.cloneNode(true);
        clone.querySelectorAll('.studio-agent-message-card').forEach(el => el.remove());
        return (clone.textContent || '').trim();
    }

    function isModelTurn(turn) {
        if (!turn) return false;
        const tag = (turn.tagName || '').toLowerCase();
        if (tag === 'ms-user-turn' || turn.classList.contains('user-turn') || turn.querySelector('.user-turn, ms-user-turn, .user-query, .author-user')) {
            return false;
        }
        if (turn.getAttribute('data-turn-role') === 'user' || turn.getAttribute('role') === 'user') {
            return false;
        }
        if (tag === 'ms-model-turn' || turn.classList.contains('model-turn') || turn.getAttribute('data-turn-role') === 'model' || turn.getAttribute('role') === 'model') {
            return true;
        }
        if (turn.querySelector('.model-turn, ms-model-turn, .model-response, [role="model"], [data-turn-role="model"]')) {
            return true;
        }
        const modelEl = turn.querySelector('.model');
        if (modelEl && !turn.querySelector('.user')) {
            return true;
        }
        return false;
    }

    function extractMessageFromText(rawText) {
        if (!rawText || !rawText.includes('"message_to_user"')) return null;

        const patterns = [
            /"message_to_user"\s*:\s*"((?:[^"\\]|\\.)*)"/,
            /"message_to_user"\s*:\s*"([\s\S]*?)(?="\s*,\s*"(?:changes|inspect_files)"|"\s*\}|$)/
        ];
        for (const pat of patterns) {
            const m = rawText.match(pat);
            if (m && m[1]) {
                try {
                    return JSON.parse(`"${m[1]}"`);
                } catch (e) {
                    return m[1].replace(/\\n/g, '\n').replace(/\\"/g, '"').trim();
                }
            }
        }
        return null;
    }

    function parseJsonFromText(rawText) {
        if (!rawText) return null;
        let text = rawText.trim();
        text = text.replace(/^\s*\d+\s*[|│]?\s*/gm, '');

        const candidates = [];

        // 1. Scan markdown fences
        const fenceRegex = /```(?:json)?\s*\n([\s\S]*?)\n```/gi;
        let m;
        while ((m = fenceRegex.exec(text)) !== null) {
            const block = m[1].trim();
            const s = block.indexOf('{');
            if (s !== -1) {
                const extracted = extractBalancedJsonObject(block, s);
                if (extracted && !candidates.includes(extracted)) candidates.push(extracted);
            }
        }

        // 2. Scan balanced objects by bracket depth
        let searchIdx = 0;
        while ((searchIdx = text.indexOf('{', searchIdx)) !== -1) {
            const extracted = extractBalancedJsonObject(text, searchIdx);
            if (extracted) {
                if (!candidates.includes(extracted)) candidates.push(extracted);
                searchIdx += extracted.length;
            } else {
                searchIdx++;
            }
        }

        // 3. Fallback: try whole string if curly braces exist
        const firstBrace = text.indexOf('{');
        const lastBrace = text.lastIndexOf('}');
        if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
            const sub = text.substring(firstBrace, lastBrace + 1);
            if (!candidates.includes(sub)) candidates.push(sub);
        }

        for (const cand of candidates) {
            // Attempt 1: Direct JSON parse
            try {
                const parsed = JSON.parse(cand);
                if (parsed && (Array.isArray(parsed.changes) || Array.isArray(parsed.inspect_files) || parsed.message_to_user)) {
                    return parsed;
                }
            } catch (e1) {}

            // Attempt 2: Smart repair & sanitization
            try {
                const sanitized = repairAndSanitizeJson(cand);
                const parsed = JSON.parse(sanitized);
                if (parsed && (Array.isArray(parsed.changes) || Array.isArray(parsed.inspect_files) || parsed.message_to_user)) {
                    return parsed;
                }
            } catch (e2) {}
        }

        if (rawText.includes('"changes"') || rawText.includes('"inspect_files"')) {
            let errorDetails = "Unknown JSON parse error";
            let errorSnippet = "";
            let isTruncated = false;

            const openBraces = (rawText.match(/\{/g) || []).length;
            const closeBraces = (rawText.match(/\}/g) || []).length;
            if (openBraces > closeBraces) {
                isTruncated = true;
            }

            try {
                JSON.parse(rawText);
            } catch (err) {
                errorDetails = err.message || String(err);
                const posMatch = errorDetails.match(/position\s+(\d+)/i);
                if (posMatch) {
                    const pos = parseInt(posMatch[1], 10);
                    const start = Math.max(0, pos - 50);
                    const end = Math.min(rawText.length, pos + 50);
                    errorSnippet = rawText.slice(start, pos) + " >>>[ERROR HERE]<<< " + rawText.slice(pos, end);
                }
            }
            console.error('[Agent JSON Error]', errorDetails, errorSnippet);
            return { 
                error: 'JSON_SYNTAX_ERROR', 
                rawText: rawText, 
                details: errorDetails, 
                snippet: errorSnippet,
                isTruncated: isTruncated 
            };
        }

        const fallbackMsg = extractMessageFromText(rawText);
        if (fallbackMsg) {
            return { message_to_user: fallbackMsg };
        }

        return null;
    }

    function getMessageFromTurnText(rawText) {
        if (!rawText || !rawText.includes('message_to_user')) return null;
        const parsed = parseJsonFromText(rawText);
        if (parsed && parsed.message_to_user && typeof parsed.message_to_user === 'string') {
            return parsed.message_to_user;
        }
        return extractMessageFromText(rawText);
    }

    function isAiGenerating() {
        const buttons = document.querySelectorAll('button');
        for (let i = buttons.length - 1; i >= 0; i--) {
            const b = buttons[i];
            if (b.offsetParent === null) continue;
            if (b.closest('#studio-agent-toolbar, #studio-file-modal')) continue;
            const txt = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
            if ((txt === 'stop' || txt.includes('stop generation')) && !txt.includes('stop sequence')) {
                return true;
            }
        }

        const progressBar = document.querySelector('mat-progress-bar, mat-spinner, .mat-mdc-progress-bar, .loading-spinner, ms-progress-bar, [role="progressbar"]');
        if (progressBar && progressBar.offsetParent !== null) return true;

        const runningRunBtn = document.querySelector('ms-run-button.running, button.running, ms-run-button [aria-label*="Stop" i], [data-test-id="stop-button"]');
        if (runningRunBtn && runningRunBtn.offsetParent !== null) return true;

        const stopIcon = document.querySelector('ms-run-button mat-icon[fonticon="stop"], ms-run-button mat-icon[data-mat-icon-name="stop"]');
        if (stopIcon && stopIcon.offsetParent !== null) return true;

        const candidateStop = findStopButton();
        if (candidateStop && candidateStop.offsetParent !== null) return true;

        return false;
    }

    function extractLatestJsonPayload(onlyLatestTurn = false) {
        const turnSelector = 'ms-chat-turn, ms-model-turn, .chat-turn, .model-turn, [data-turn-role]';
        let turns = Array.from(document.querySelectorAll(turnSelector)).filter(t => isModelTurn(t));
        if (turns.length === 0) {
            turns = Array.from(document.querySelectorAll('ms-model-turn, .model-turn, ms-chat-turn'));
        }
        if (turns.length === 0) return null;

        if (onlyLatestTurn) {
            const lastTurn = turns[turns.length - 1];
            const text = getTurnRawText(lastTurn);
            if (!text || (!text.includes('changes') && !text.includes('message_to_user') && !text.includes('inspect_files'))) {
                return null;
            }
            return parseJsonFromText(text);
        }

        for (let i = turns.length - 1; i >= 0; i--) {
            const turn = turns[i];
            const text = getTurnRawText(turn);
            if (!text || (!text.includes('changes') && !text.includes('message_to_user') && !text.includes('inspect_files'))) {
                continue;
            }
            const parsed = parseJsonFromText(text);
            if (parsed && (Array.isArray(parsed.changes) || Array.isArray(parsed.inspect_files) || parsed.message_to_user)) {
                return parsed;
            }
        }
        return null;
    }

    function appendInlineFormattedText(parent, text) {
        const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
        for (const token of tokens) {
            if (!token) continue;
            if (token.startsWith('`') && token.endsWith('`') && token.length >= 2) {
                const c = createEl('code', {
                    background: 'rgba(110,118,129,0.4)',
                    padding: '2px 5px',
                    borderRadius: '4px',
                    fontFamily: 'monospace',
                    fontSize: '11.5px',
                    color: '#f0f6fc'
                }, token.slice(1, -1));
                parent.appendChild(c);
            } else if (token.startsWith('**') && token.endsWith('**') && token.length >= 4) {
                const b = createEl('strong', { color: '#fff' }, token.slice(2, -2));
                parent.appendChild(b);
            } else {
                parent.appendChild(document.createTextNode(token));
            }
        }
    }

    function renderSafeMarkdown(rawText, container) {
        safeClear(container);
        if (!rawText) return;

        const lines = rawText.split('\n');
        let inCodeBlock = false;
        let codeLines = [];

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];

            if (line.trim().startsWith('```')) {
                if (inCodeBlock) {
                    const pre = createEl('pre', {
                        background: '#0d1117',
                        border: '1px solid #30363d',
                        padding: '8px 12px',
                        borderRadius: '6px',
                        overflowX: 'auto',
                        margin: '6px 0',
                        fontFamily: 'monospace',
                        fontSize: '12px',
                        color: '#e6edf3'
                    });
                    const code = createEl('code', {}, codeLines.join('\n'));
                    pre.appendChild(code);
                    container.appendChild(pre);
                    codeLines = [];
                    inCodeBlock = false;
                } else {
                    inCodeBlock = true;
                    codeLines = [];
                }
                continue;
            }

            if (inCodeBlock) {
                codeLines.push(line);
                continue;
            }

            if (line.startsWith('### ')) {
                const h = createEl('h4', { margin: '8px 0 4px', fontSize: '13px', color: '#58a6ff' }, line.slice(4));
                container.appendChild(h);
            } else if (line.startsWith('## ')) {
                const h = createEl('h3', { margin: '10px 0 4px', fontSize: '14px', color: '#79c0ff' }, line.slice(3));
                container.appendChild(h);
            } else if (line.startsWith('# ')) {
                const h = createEl('h2', { margin: '12px 0 6px', fontSize: '15px', color: '#fff', borderBottom: '1px solid #30363d', paddingBottom: '3px' }, line.slice(2));
                container.appendChild(h);
            } else if (line.startsWith('> ')) {
                const bq = createEl('blockquote', { borderLeft: '3px solid #58a6ff', margin: '4px 0', padding: '2px 10px', color: '#8b949e', background: 'rgba(56,139,253,0.05)' }, line.slice(2));
                container.appendChild(bq);
            } else if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
                const li = createEl('div', { marginLeft: '16px', marginBottom: '2px', display: 'flex', gap: '6px' });
                const bullet = createEl('span', { color: '#58a6ff' }, '•');
                const textSpan = createEl('span', {});
                appendInlineFormattedText(textSpan, line.trim().slice(2));
                li.appendChild(bullet);
                li.appendChild(textSpan);
                container.appendChild(li);
            } else if (line.trim() === '') {
                container.appendChild(createEl('div', { height: '6px' }));
            } else {
                const p = createEl('div', { margin: '2px 0', lineHeight: '1.5' });
                appendInlineFormattedText(p, line);
                container.appendChild(p);
            }
        }

        if (inCodeBlock && codeLines.length > 0) {
            const pre = createEl('pre', {
                background: '#0d1117',
                border: '1px solid #30363d',
                padding: '8px 12px',
                borderRadius: '6px',
                overflowX: 'auto',
                margin: '6px 0',
                fontFamily: 'monospace',
                fontSize: '12px',
                color: '#e6edf3'
            });
            pre.appendChild(createEl('code', {}, codeLines.join('\n')));
            container.appendChild(pre);
        }
    }

    function renderUnifiedDiff(container, diffText) {
        safeClear(container);
        if (!diffText || !diffText.trim()) {
            container.appendChild(createEl('div', { padding: '6px 10px', color: '#8b949e', fontStyle: 'italic' }, 'Identical content or empty file'));
            return;
        }
        diffText.split('\n').forEach(l => {
            if (l.startsWith('@@') && l.includes('@@', 2)) {
                container.appendChild(createEl('div', { background: 'rgba(56,139,253,0.1)', color: '#79c0ff', fontWeight: '600', padding: '2px 8px' }, l));
            } else if (l.startsWith('+')) {
                const row = createEl('div', { background: 'rgba(46,160,67,0.15)', color: '#7ee787', padding: '1px 8px' });
                row.appendChild(createEl('span', { color: '#56d364', fontWeight: 'bold', marginRight: '6px' }, '+'));
                row.appendChild(document.createTextNode(l.substring(1)));
                container.appendChild(row);
            } else if (l.startsWith('-')) {
                const row = createEl('div', { background: 'rgba(248,81,73,0.15)', color: '#ffa198', padding: '1px 8px' });
                row.appendChild(createEl('span', { color: '#f85149', fontWeight: 'bold', marginRight: '6px' }, '-'));
                row.appendChild(document.createTextNode(l.substring(1)));
                container.appendChild(row);
            } else {
                const row = createEl('div', { color: '#c9d1d9', padding: '1px 8px' });
                row.appendChild(createEl('span', { marginRight: '6px', opacity: '0.3' }, ' '));
                row.appendChild(document.createTextNode(l.startsWith(' ') ? l.substring(1) : l));
                container.appendChild(row);
            }
        });
    }

    function computeLineDiff(oldStr, newStr) {
        const oldLines = oldStr ? oldStr.split('\n') : [];
        const newLines = newStr ? newStr.split('\n') : [];
        if (oldLines.length === 0 && newLines.length === 0) return { diffText: '', additions: 0, deletions: 0 };
        if (oldLines.length === 0) {
            return {
                diffText: newLines.map(l => '+' + l).join('\n'),
                additions: newLines.length,
                deletions: 0
            };
        }
        if (newLines.length === 0) {
            return {
                diffText: oldLines.map(l => '-' + l).join('\n'),
                additions: 0,
                deletions: oldLines.length
            };
        }
        const m = oldLines.length;
        const n = newLines.length;
        if (m * n > 250000) {
            return {
                diffText: oldLines.map(l => '-' + l).concat(newLines.map(l => '+' + l)).join('\n'),
                additions: newLines.length,
                deletions: oldLines.length
            };
        }
        const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
        for (let i = 0; i < m; i++) {
            for (let j = 0; j < n; j++) {
                if (oldLines[i] === newLines[j]) {
                    dp[i + 1][j + 1] = dp[i][j] + 1;
                } else {
                    dp[i + 1][j + 1] = Math.max(dp[i + 1][j], dp[i][j + 1]);
                }
            }
        }
        let i = m, j = n;
        const diffItems = [];
        while (i > 0 || j > 0) {
            if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
                diffItems.push({ type: ' ', text: oldLines[i - 1] });
                i--; j--;
            } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
                diffItems.push({ type: '+', text: newLines[j - 1] });
                j--;
            } else if (i > 0 && (j === 0 || dp[i][j - 1] < dp[i - 1][j])) {
                diffItems.push({ type: '-', text: oldLines[i - 1] });
                i--;
            }
        }
        diffItems.reverse();
        let additions = 0, deletions = 0;
        const diffLines = diffItems.map(item => {
            if (item.type === '+') additions++;
            if (item.type === '-') deletions++;
            return item.type + item.text;
        });
        return {
            diffText: diffLines.join('\n'),
            additions,
            deletions
        };
    }

    function computeFileStats(f, transData, idx = -1) {
        if (f && f.diff !== undefined && (f.additions !== undefined || f.deletions !== undefined)) {
            return {
                additions: f.additions || 0,
                deletions: f.deletions || 0,
                status: f.status || null,
                diff: f.diff || null,
                syntax_error: f.syntax_error || null
            };
        }
        if (transData && Array.isArray(transData.files)) {
            let matched = (idx >= 0 && idx < transData.files.length) ? transData.files[idx] : null;
            if (!matched || matched.file_path !== f.file_path) {
                matched = transData.files.find(tf => tf.file_path === f.file_path && tf.action === f.action && tf.comment === f.comment) ||
                          transData.files.find(tf => tf.file_path === f.file_path);
            }
            if (matched && (matched.additions !== undefined || matched.deletions !== undefined)) {
                return {
                    additions: matched.additions || 0,
                    deletions: matched.deletions || 0,
                    status: matched.status || null,
                    diff: matched.diff || null,
                    syntax_error: matched.syntax_error || null
                };
            }
        }
        const action = (f.action || 'overwrite').toLowerCase();
        const search = f.search || '';
        const content = f.content || '';
        if (action === 'mkdir') return { additions: 0, deletions: 0 };
        if (action === 'delete') return { additions: 0, deletions: 0, isDelete: true };
        if (action === 'append' || action === 'overwrite') {
            const adds = content ? content.split('\n').length : 0;
            return { additions: adds, deletions: 0 };
        }
        if (action === 'patch') {
            if (search) {
                const dRes = computeLineDiff(search, content);
                return { additions: dRes.additions, deletions: dRes.deletions, previewDiff: dRes.diffText };
            }
            const adds = content ? content.split('\n').length : 0;
            return { additions: adds, deletions: 0 };
        }
        return { additions: 0, deletions: 0 };
    }

    function renderPreviewDiff(container, change) {
        safeClear(container);
        const action = (change.action || 'overwrite').toLowerCase();
        const search = change.search || '';
        const anchorStart = change.anchor_start || '';
        const anchorEnd = change.anchor_end || '';
        const content = change.content || '';

        if (anchorStart && anchorEnd) {
            container.appendChild(createEl('div', { background: 'rgba(56, 139, 253, 0.1)', color: '#79c0ff', fontWeight: '600', padding: '3px 8px', fontSize: '11px' }, `@@ Range [${anchorStart.trim().slice(0, 30)} ... ${anchorEnd.trim().slice(0, 30)}] @@`));
        }

        if (action === 'mkdir') {
            container.appendChild(createEl('div', { padding: '8px 12px', color: '#d29922', fontStyle: 'italic', fontSize: '12px' }, `Directory will be created: ${change.file_path}`));
            return;
        }
        if (action === 'delete') {
            container.appendChild(createEl('div', { padding: '8px 12px', color: '#f85149', fontStyle: 'italic', fontSize: '12px' }, `File will be deleted: ${change.file_path}`));
            return;
        }

        if (action === 'append') {
            container.appendChild(createEl('div', { background: 'rgba(163, 113, 247, 0.1)', color: '#d2a8ff', fontWeight: '600', padding: '3px 8px', fontSize: '11px' }, '@@ Append to end of file @@'));
            if (content) renderUnifiedDiff(container, content.split('\n').map(l => '+' + l).join('\n'));
            return;
        } else if (action === 'overwrite') {
            container.appendChild(createEl('div', { background: 'rgba(56, 139, 253, 0.1)', color: '#79c0ff', fontWeight: '600', padding: '3px 8px', fontSize: '11px' }, '@@ Overwrite / Create file @@'));
            if (content) renderUnifiedDiff(container, content.split('\n').map(l => '+' + l).join('\n'));
            return;
        } else if (action === 'patch') {
            container.appendChild(createEl('div', { background: 'rgba(56, 139, 253, 0.1)', color: '#79c0ff', fontWeight: '600', padding: '3px 8px', fontSize: '11px' }, '@@ Targeted replacement @@'));
            if (search) {
                const dRes = computeLineDiff(search, content);
                renderUnifiedDiff(container, dRes.diffText);
                return;
            }
            if (content) {
                renderUnifiedDiff(container, content.split('\n').map(l => '+' + l).join('\n'));
                return;
            }
        }
        container.appendChild(createEl('div', { padding: '8px 12px', color: '#8b949e', fontStyle: 'italic' }, 'No textual changes'));
    }

    function createMessageCard(msgText, changes = [], cardStatus = 'preview', transData = null, transRecord = null) {
        const isApplied = cardStatus === 'applied';
        const isFailed = cardStatus === 'failed';
        const isPartial = cardStatus === 'partial';

        const theme = isApplied ? {
            accent: '#3fb950',
            border: '#30363d',
            borderLeft: '4px solid #3fb950',
            badgeBg: 'rgba(63, 185, 80, 0.15)',
            badgeBorder: 'rgba(63, 185, 80, 0.35)',
            badgeColor: '#3fb950',
            badgeText: 'APPLIED',
            icon: '✓'
        } : isFailed ? {
            accent: '#f85149',
            border: 'rgba(248, 81, 73, 0.4)',
            borderLeft: '4px solid #f85149',
            badgeBg: 'rgba(248, 81, 73, 0.15)',
            badgeBorder: 'rgba(248, 81, 73, 0.4)',
            badgeColor: '#f85149',
            badgeText: 'FAILED',
            icon: '✕'
        } : isPartial ? {
            accent: '#d29922',
            border: 'rgba(210, 153, 34, 0.4)',
            borderLeft: '4px solid #d29922',
            badgeBg: 'rgba(210, 153, 34, 0.15)',
            badgeBorder: 'rgba(210, 153, 34, 0.4)',
            badgeColor: '#d29922',
            badgeText: 'PARTIAL',
            icon: '⚠'
        } : {
            accent: '#58a6ff',
            border: '#30363d',
            borderLeft: '4px solid #58a6ff',
            badgeBg: 'rgba(56, 139, 253, 0.15)',
            badgeBorder: 'rgba(56, 139, 253, 0.3)',
            badgeColor: '#58a6ff',
            badgeText: 'PROPOSED',
            icon: '✦'
        };

        const card = createEl('div', {
            background: '#161b22',
            border: `1px solid ${theme.border}`,
            borderLeft: theme.borderLeft,
            borderRadius: '8px',
            padding: '12px 16px',
            margin: '14px 0 10px 0',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            boxShadow: '0 4px 18px rgba(0,0,0,0.3)',
            width: '100%',
            boxSizing: 'border-box',
            display: 'block',
            position: 'relative',
            zIndex: '10'
        });
        card.className = 'studio-agent-message-card';

        const head = createEl('div', {
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: msgText ? '8px' : '0',
            paddingBottom: msgText ? '6px' : '0',
            borderBottom: msgText ? '1px solid rgba(48, 54, 61, 0.6)' : 'none'
        });

        const headTitle = createEl('div', {
            color: theme.accent,
            fontWeight: '700',
            fontSize: '11.5px',
            textTransform: 'uppercase',
            letterSpacing: '0.6px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
        });
        headTitle.appendChild(createEl('span', { fontSize: '12px' }, theme.icon));
        headTitle.appendChild(createEl('span', {}, 'Agent Note'));
        head.appendChild(headTitle);

        const statusBadge = createEl('span', {
            fontSize: '10px',
            fontWeight: '700',
            padding: '2px 7px',
            borderRadius: '4px',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
            background: theme.badgeBg,
            color: theme.badgeColor,
            border: `1px solid ${theme.badgeBorder}`
        }, theme.badgeText);
        head.appendChild(statusBadge);
        card.appendChild(head);

        if (transRecord && transRecord.errors && transRecord.errors.length > 0) {
            const errBox = createEl('div', {
                background: 'rgba(248, 81, 73, 0.1)',
                border: '1px solid rgba(248, 81, 73, 0.35)',
                borderRadius: '6px',
                padding: '8px 12px',
                margin: '8px 0',
                fontSize: '12px',
                color: '#ffa198'
            });
            const errTitle = createEl('div', { fontWeight: '700', color: '#f85149', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '6px' });
            errTitle.appendChild(createEl('span', {}, '⚠'));
            errTitle.appendChild(createEl('span', {}, 'Patch Execution Error:'));
            errBox.appendChild(errTitle);
            transRecord.errors.forEach(errText => {
                const errRow = createEl('div', { fontFamily: 'ui-monospace, SFMono-Regular, monospace', fontSize: '11px', whiteSpace: 'pre-wrap', lineHeight: '1.4', marginBottom: '4px' }, errText);
                errBox.appendChild(errRow);
            });
            card.appendChild(errBox);
        }

        if (transRecord && transRecord.warnings && transRecord.warnings.length > 0) {
            const warnBox = createEl('div', {
                background: 'rgba(210, 153, 34, 0.1)',
                border: '1px solid rgba(210, 153, 34, 0.35)',
                borderRadius: '6px',
                padding: '8px 12px',
                margin: '8px 0',
                fontSize: '12px',
                color: '#e3b341'
            });
            const warnTitle = createEl('div', { fontWeight: '700', color: '#d29922', marginBottom: '4px' }, 'Validation Warnings:');
            warnBox.appendChild(warnTitle);
            transRecord.warnings.forEach(warnText => {
                warnBox.appendChild(createEl('div', { fontFamily: 'ui-monospace, SFMono-Regular, monospace', fontSize: '11px', whiteSpace: 'pre-wrap', lineHeight: '1.4' }, warnText));
            });
            card.appendChild(warnBox);
        }

        if (msgText) {
            const body = createEl('div', {
                color: '#e6edf3',
                fontSize: '13px',
                lineHeight: '1.55',
                marginTop: '6px'
            });
            body.className = 'studio-agent-card-body';
            renderSafeMarkdown(msgText, body);
            card.appendChild(body);
        }

        const filesToDisplay = (transData && transData.files) ? transData.files : changes;
        if (filesToDisplay && filesToDisplay.length > 0) {
            let totalAdditions = 0;
            let totalDeletions = 0;
            const enrichedFiles = filesToDisplay.map((f, idx) => {
                const stats = computeFileStats(f, transData, idx);
                totalAdditions += (stats.additions || 0);
                totalDeletions += (stats.deletions || 0);
                return { ...f, ...stats, _uniqueIdx: idx };
            });

            const diffWrap = createEl('div', {
                marginTop: msgText ? '12px' : '6px',
                paddingTop: msgText ? '10px' : '0',
                borderTop: msgText ? '1px solid rgba(48, 54, 61, 0.6)' : 'none',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px'
            });

            const diffHeader = createEl('div', {
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                fontSize: '12px',
                fontWeight: '600'
            });

            const headerLeft = createEl('div', {
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                color: '#c9d1d9'
            });
            headerLeft.appendChild(createEl('span', {}, ''));
            const sectionLabel = isApplied ? 'Changes Applied' : isFailed ? 'Proposed Changes (Failed)' : 'Proposed Changes';
            const uniqueFilesCount = new Set(enrichedFiles.map(f => f.file_path)).size;
            const editsNote = enrichedFiles.length > uniqueFilesCount ? ` (${enrichedFiles.length} edits)` : '';
            headerLeft.appendChild(createEl('span', {}, `${sectionLabel} (${uniqueFilesCount} file${uniqueFilesCount > 1 ? 's' : ''}${editsNote})`));

            if (totalAdditions > 0 || totalDeletions > 0) {
                const statsPill = createEl('span', {
                    fontFamily: 'ui-monospace, SFMono-Regular, monospace',
                    fontSize: '11px',
                    fontWeight: '700',
                    display: 'inline-flex',
                    gap: '4px',
                    marginLeft: '4px',
                    padding: '1px 6px',
                    borderRadius: '3px',
                    background: 'rgba(110, 118, 129, 0.2)'
                });
                if (totalAdditions > 0) statsPill.appendChild(createEl('span', { color: '#3fb950' }, `+${totalAdditions}`));
                if (totalDeletions > 0) statsPill.appendChild(createEl('span', { color: '#f85149' }, `-${totalDeletions}`));
                headerLeft.appendChild(statsPill);
            }

            diffHeader.appendChild(headerLeft);
            diffWrap.appendChild(diffHeader);

            enrichedFiles.forEach((f) => {
                const filePath = f.file_path || '';
                const action = (f.action || (f.status === 'A' ? 'overwrite' : f.status === 'D' ? 'delete' : f.status === 'DIR' ? 'mkdir' : 'patch')).toLowerCase();
                const badgeText = action === 'mkdir' ? 'DIR' : action === 'overwrite' ? 'NEW' : action === 'append' ? 'APP' : action === 'delete' ? 'DEL' : 'MOD';
                const badgeColor = action === 'mkdir' ? '#d29922' : action === 'overwrite' ? '#3fb950' : action === 'append' ? '#a371f7' : action === 'delete' ? '#f85149' : '#58a6ff';

                const fCard = createEl('div', {
                    background: '#0d1117',
                    border: '1px solid #30363d',
                    borderRadius: '6px',
                    overflow: 'visible',
                    position: 'relative'
                });

                const fBar = createEl('div', {
                    background: '#161b22',
                    padding: '6px 10px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    cursor: 'pointer',
                    userSelect: 'none',
                    fontSize: '11.5px',
                    position: 'sticky',
                    top: '0',
                    zIndex: '5',
                    borderBottom: '1px solid #30363d'
                });

                const leftInfo = createEl('div', {
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontFamily: 'monospace',
                    color: '#e6edf3'
                });
                const toggleIcon = createEl('span', { color: '#8b949e', fontSize: '9px', width: '12px' }, '▶');
                const b = createEl('span', {
                    fontSize: '9px',
                    fontWeight: '700',
                    padding: '1px 5px',
                    borderRadius: '3px',
                    color: badgeColor,
                    border: `1px solid ${badgeColor}40`
                }, badgeText);
                const fName = createEl('span', { fontWeight: '600' }, filePath);
                leftInfo.appendChild(toggleIcon);
                leftInfo.appendChild(b);
                leftInfo.appendChild(fName);

                if (f.comment) {
                    const commentSpan = createEl('span', { color: '#8b949e', fontSize: '11px', marginLeft: '6px', fontStyle: 'italic' }, `// ${f.comment}`);
                    leftInfo.appendChild(commentSpan);
                }

                const rightInfo = createEl('div', { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px', flexShrink: '0' });

                const hasFileError = transRecord && transRecord.errors && transRecord.errors.some(e => e.includes(filePath));
                if (hasFileError) {
                    rightInfo.appendChild(createEl('span', {
                        fontSize: '9px', fontWeight: '700', padding: '1px 5px', borderRadius: '3px',
                        color: '#f85149', background: 'rgba(248, 81, 73, 0.15)', border: '1px solid rgba(248, 81, 73, 0.3)'
                    }, 'FAILED'));
                } else if (f.reverted) {
                    rightInfo.appendChild(createEl('span', {
                        fontSize: '9px', fontWeight: '700', padding: '1px 5px', borderRadius: '3px',
                        color: '#f85149', background: 'rgba(248, 81, 73, 0.15)', border: '1px solid rgba(248, 81, 73, 0.3)'
                    }, 'REVERTED'));
                }

                if (f.additions || f.deletions) {
                    if (f.additions) rightInfo.appendChild(createEl('span', { color: '#3fb950', fontWeight: '700', fontFamily: 'monospace' }, `+${f.additions}`));
                    if (f.deletions) rightInfo.appendChild(createEl('span', { color: '#f85149', fontWeight: '700', fontFamily: 'monospace' }, `-${f.deletions}`));
                } else if (f.isDelete) {
                    rightInfo.appendChild(createEl('span', { color: '#f85149', fontWeight: '600' }, 'Delete'));
                }

                fBar.appendChild(leftInfo);
                fBar.appendChild(rightInfo);

                const codeBody = createEl('div', {
                    display: 'none',
                    fontFamily: 'ui-monospace, SFMono-Regular, monospace',
                    fontSize: '11px',
                    lineHeight: '1.45',
                    overflowX: 'auto',
                    overflowY: 'auto',
                    maxHeight: '480px'
                });

                if (f.diff) {
                    renderUnifiedDiff(codeBody, f.diff);
                } else {
                    renderPreviewDiff(codeBody, f);
                }

                const toggleDiff = () => {
                    const isOpen = codeBody.style.display === 'block';
                    codeBody.style.display = isOpen ? 'none' : 'block';
                    toggleIcon.textContent = isOpen ? '▶' : '▼';
                    if (bottomCollapse) bottomCollapse.style.display = isOpen ? 'none' : 'block';
                };

                fBar.onclick = toggleDiff;

                const bottomCollapse = createEl('div', {
                    background: '#161b22',
                    padding: '6px 10px',
                    display: 'none',
                    textAlign: 'center',
                    cursor: 'pointer',
                    color: '#8b949e',
                    fontSize: '11px',
                    borderTop: '1px solid #30363d',
                    fontWeight: '600'
                }, '▲ Collapse diff');
                bottomCollapse.onclick = toggleDiff;
                codeBody.appendChild(bottomCollapse);

                fCard.appendChild(fBar);
                fCard.appendChild(codeBody);
                diffWrap.appendChild(fCard);
            });

            card.appendChild(diffWrap);
        }

        return card;
    }

    function renderInChatMessageCards() {
        const turnSelector = 'ms-chat-turn, ms-model-turn, .chat-turn, .model-turn, [data-turn-role]';
        document.querySelectorAll(turnSelector).forEach(turn => {
            if (!isModelTurn(turn)) {
                const leaked = turn.querySelector('.studio-agent-message-card');
                if (leaked) leaked.remove();
            }
        });

        const turns = Array.from(document.querySelectorAll(turnSelector)).filter(t => isModelTurn(t));

        turns.forEach(turn => {
            const text = getTurnRawText(turn);
            const cachedCard = turn.querySelector('.studio-agent-message-card');
            if (cachedCard && turn.dataset.cachedLen === String(text.length)) return;
            turn.dataset.cachedLen = String(text.length);
            if (!text.includes('message_to_user')) {
                const oldCard = turn.querySelector('.studio-agent-message-card');
                if (oldCard) oldCard.remove();
                return;
            }

            const parsed = parseJsonFromText(text);
            const msg = (parsed && parsed.message_to_user) ? parsed.message_to_user : getMessageFromTurnText(text);
            const changes = (parsed && Array.isArray(parsed.changes)) ? parsed.changes : [];
            if (!msg && changes.length === 0) {
                const oldCard = turn.querySelector('.studio-agent-message-card');
                if (oldCard) oldCard.remove();
                return;
            }

            const changesSig = getChangesSignature(changes);
            const transRecord = changesSig ? transactionStates[changesSig] : null;
            let cardStatus = 'preview';
            if (transRecord) {
                cardStatus = transRecord.status;
            } else if (changesSig && (appliedSignatures.has(changesSig) || changesSig === lastAppliedSignature)) {
                cardStatus = 'applied';
            }

            let transData = (transRecord && transRecord.transaction) ? transRecord.transaction : (changesSig ? appliedTransactions[changesSig] : null);

            if (cardStatus === 'preview' && isServerOnline && changes.length > 0 && !appliedTransactions[changesSig]) {
                const previewCacheKey = `preview_${changesSig}`;
                if (!appliedTransactions[previewCacheKey]) {
                    appliedTransactions[previewCacheKey] = 'pending';
                    GM_xmlhttpRequest({
                        method: 'POST',
                        url: `${getServerUrl()}/api/preview_changes`,
                        headers: { 'Content-Type': 'application/json' },
                        data: JSON.stringify({ changes }),
                        onload: (r) => {
                            try {
                                const d = JSON.parse(r.responseText);
                                if (d && Array.isArray(d.files) && d.files.length > 0) {
                                    appliedTransactions[previewCacheKey] = { files: d.files };
                                    renderInChatMessageCards();
                                }
                            } catch (e) {}
                        }
                    });
                } else if (appliedTransactions[previewCacheKey] && appliedTransactions[previewCacheKey] !== 'pending') {
                    transData = appliedTransactions[previewCacheKey];
                }
            }

            const hasPreview = Boolean(transData && transData.files && transData.files.length > 0);
            const cardSig = `${msg || ''}_${changesSig}_${cardStatus}_${hasPreview ? 'p1' : 'p0'}_${transRecord ? JSON.stringify(transRecord.errors || []) : ''}`;

            const messageContainer = turn.querySelector('.chat-turn-container') || turn;
            const existingCard = messageContainer.querySelector('.studio-agent-message-card');
            if (existingCard) {
                if (existingCard.dataset.sig !== cardSig) {
                    existingCard.remove();
                } else {
                    return;
                }
            }

            const card = createMessageCard(msg, changes, cardStatus, transData, transRecord);
            card.dataset.sig = cardSig;
            messageContainer.appendChild(card);
        });
    }

    async function handleAutoInspect(inspectFiles) {
        if (!inspectFiles || inspectFiles.length === 0 || isOperationInProgress) return;

        const sig = JSON.stringify(inspectFiles);
        if (sig === lastHandledInspectSig && autoInspectStreak > 0) return;
        lastHandledInspectSig = sig;

        if (autoInspectStreak >= MAX_AUTO_INSPECT) {
            showToast('Auto-pilot paused: Loop limit reached', true);
            autoInspectStreak = 0;
            return;
        }

        isOperationInProgress = true;
        updateApplyButtonUI();
        autoInspectStreak++;
        showToast(`Fetching ${inspectFiles.length} file(s)...`);

        let responsePrompt = `[TOOL RESULT: Project File Contents]\n\n`;

        for (const file of inspectFiles) {
            try {
                const res = await new Promise(resolve => {
                    GM_xmlhttpRequest({
                        method: 'GET',
                        url: `${getServerUrl()}/api/file_content?path=${encodeURIComponent(file)}`,
                        onload: r => {
                            try { resolve(JSON.parse(r.responseText)); } catch (e) { resolve(null); }
                        },
                        onerror: () => resolve(null)
                    });
                });

                if (res && res.content) {
                    responsePrompt += `--- File: ${file} ---\n\`\`\`\n${res.content}\n\`\`\`\n\n`;
                } else {
                    responsePrompt += `--- File: ${file} (NOT FOUND or EMPTY) ---\n\n`;
                }
            } catch (e) {
                responsePrompt += `--- File: ${file} (Read Error) ---\n\n`;
            }
        }

        responsePrompt += `Files provided. Proceed with your task and output the requested changes in JSON.`;
        try {
            await submitPrompt(responsePrompt);
            showToast(`Submitted ${inspectFiles.join(', ')} to Agent`);
        } finally {
            isOperationInProgress = false;
            updateApplyButtonUI();
        }
    }

    function updateApplyButtonUI() {
        const btn = document.getElementById('studio-btn-apply');
        if (!btn) return;
        if (isAiGenerating()) {
            btn.disabled = true;
            btn.textContent = 'Generating...';
            return;
        }
        if (isOperationInProgress) {
            btn.disabled = true;
            btn.textContent = 'Applying...';
            return;
        }
        if (isQueued) {
            btn.disabled = false;
            btn.textContent = 'Queued...';
            return;
        }
        btn.disabled = false;
        const payload = extractLatestJsonPayload(true);
        if (payload && Array.isArray(payload.inspect_files) && payload.inspect_files.length > 0) {
            const inspectSig = JSON.stringify(payload.inspect_files);
            if (inspectSig !== lastHandledInspectSig) {
                btn.textContent = `Inspect (${payload.inspect_files.length})`;
                return;
            }
        }
        btn.textContent = 'Apply';
    }

    async function sendChangesToServer(payload, btnElement = null, isAuto = false) {
        if (!payload || isOperationInProgress) return;

        const signature = getChangesSignature(payload.changes);
        if (appliedSignatures.has(signature) || signature === lastAppliedSignature || (isAuto && signature === lastProcessedChangesSig)) {
            if (!isAuto) showToast('Changes already applied', false);
            return;
        }
        lastProcessedChangesSig = signature;

        isOperationInProgress = true;
        updateApplyButtonUI();

        const deleteActions = (payload.changes || []).filter(c => (c.action || '').toLowerCase() === 'delete');
        if (deleteActions.length > 0) {
            const fileList = deleteActions.map(c => `• ${c.file_path}`).join('\n');
            const ok = window.confirm(`CONFIRM FILE DELETION\n\nThe Agent requested to DELETE the following file(s):\n\n${fileList}\n\nAre you sure you want to delete these files?`);
            if (!ok) {
                isOperationInProgress = false;
                updateApplyButtonUI();
                showToast('Deletion cancelled by user');
                return;
            } else {
                deleteActions.forEach(c => c.confirmed = true);
            }
        }

        return new Promise((resolve) => {
            GM_xmlhttpRequest({
                method: 'POST',
                url: `${getServerUrl()}/api/apply_changes`,
                headers: { 'Content-Type': 'application/json' },
                data: JSON.stringify(payload),
                timeout: 15000,
                ontimeout: () => {
                    isOperationInProgress = false;
                    updateApplyButtonUI();
                    showToast('Request timed out (15s)', true);
                    resolve();
                },
                onload: (res) => {
                    isOperationInProgress = false;
                    updateApplyButtonUI();

                    if (res.status === 200) {
                        try {
                            const d = JSON.parse(res.responseText);
                            const hasErrors = d.errors && d.errors.length > 0;
                            const hasUpdated = d.updated && d.updated.length > 0;
                            let cardStatus = 'applied';
                            if (hasErrors && !hasUpdated) {
                                cardStatus = 'failed';
                            } else if (hasErrors && hasUpdated) {
                                cardStatus = 'partial';
                            }

                            transactionStates[signature] = {
                                status: cardStatus,
                                transaction: d.transaction || null,
                                errors: d.errors || [],
                                warnings: d.warnings || [],
                                updated: d.updated || []
                            };

                            if (cardStatus !== 'failed') {
                                lastAppliedSignature = signature;
                                GM_setValue('last_applied_signature', signature);
                                appliedSignatures.add(signature);
                                if (d.transaction) {
                                    appliedTransactions[signature] = d.transaction;
                                }
                            }

                            renderInChatMessageCards();
                            if (hasErrors) {
                                console.error('[Agent Bridge Errors]', d.errors);
                                showToast(`Error: ${d.errors[0]}`, true);
                                if (isAgentActive && (!isAuto || isAutoApply) && patchRetryCount < MAX_PATCH_RETRIES) {
                                    patchRetryCount++;
                                    showToast(`Patch error. Requesting Agent self-repair (${patchRetryCount}/${MAX_PATCH_RETRIES})...`);
                                    const recoveryPrompt = `[TOOL RESULT: Patch Application Error]\n` +
                                        `The bridge server rejected your patch with error:\n` +
                                        `${d.errors.join('\n')}\n\n` +
                                        `STRICT CORRECTION INSTRUCTIONS:\n` +
                                        `1. Your previous 'search' block was REJECTED because it was too large or failed to match.\n` +
                                        `2. Provide a MICRO-PATCH: 'search' MUST be ONLY 2 to 5 lines of verbatim code.\n` +
                                        `3. Or use 'anchor_start' (1-2 lines) and 'anchor_end' (1-2 lines).\n` +
                                        `4. NEVER include whole functions. Split into multiple items in 'changes' if needed.`;
                                    submitPrompt(recoveryPrompt).then(ok => {
                                        if (ok) showToast(`Self-repair prompt sent (${patchRetryCount}/${MAX_PATCH_RETRIES})`);
                                    });
                                } else if (patchRetryCount >= MAX_PATCH_RETRIES) {
                                    patchRetryCount = 0;
                                    showToast('Patch failed: auto-repair stopped to prevent loops', true);
                                }
                            } else {
                                patchRetryCount = 0;
                                autoInspectStreak = 0;
                                if (d.warnings && d.warnings.length > 0) {
                                    showToast(`Warning: ${d.warnings[0]}`, true);
                                } else {
                                    showToast(`Applied ${d.updated.length} file(s) successfully`);
                                }
                            }
                        } catch (e) {
                            showToast('Error parsing server response', true);
                        }
                    } else {
                        transactionStates[signature] = {
                            status: 'failed',
                            transaction: null,
                            errors: [`Server responded with HTTP ${res.status}`],
                            warnings: [],
                            updated: []
                        };
                        renderInChatMessageCards();
                        showToast('Failed to apply file changes', true);
                    }
                    resolve();
                },
                onerror: () => {
                    isOperationInProgress = false;
                    updateApplyButtonUI();
                    transactionStates[signature] = {
                        status: 'failed',
                        transaction: null,
                        errors: [`Bridge server offline on port ${currentPort}`],
                        warnings: [],
                        updated: []
                    };
                    renderInChatMessageCards();
                    showToast(`Server offline on port ${currentPort}`, true);
                    resolve();
                }
            });
        });
    }

    async function handleApplyClick(btnElement, isAuto = false) {
        if (isOperationInProgress) return;
        if (isAuto && !isAutoApply) return;

        if (isAiGenerating()) {
            if (isAuto) return;
            if (isQueued) return;
            isQueued = true;
            updateApplyButtonUI();
            showToast('Generating... Auto-applying when finished');
            return;
        }

        const payload = extractLatestJsonPayload(true);
        if (!payload) {
            if (!isAuto) showToast('No valid JSON response found in chat', true);
            return;
        }

        if (payload.error === 'JSON_SYNTAX_ERROR') {
            const errSig = (payload.details || '') + (payload.snippet || '');
            if (errSig === lastHandledErrorSig && isAuto) return;
            lastHandledErrorSig = errSig;
            showToast('JSON Syntax Error: failed to parse changes', true);
            if (isAgentActive && patchRetryCount < MAX_PATCH_RETRIES) {
                patchRetryCount++;
                isOperationInProgress = true;
                updateApplyButtonUI();
                showToast(`Requesting JSON repair (${patchRetryCount}/${MAX_PATCH_RETRIES})...`);
                
                let diagnostic = `[TOOL RESULT: JSON Syntax Error]\n` +
                    `Your previous output could not be parsed as valid JSON.\n` +
                    `Parser Error: ${payload.details || 'SyntaxError'}\n`;

                if (payload.isTruncated) {
                    diagnostic += `CRITICAL: Your output was CUT OFF mid-stream (hit max token limit!).\n` +
                        `Please split your changes into smaller chunks or output fewer files at once.\n`;
                }

                if (payload.snippet) {
                    diagnostic += `Problematic area:\n\`\`\`\n${payload.snippet}\n\`\`\`\n`;
                }

                diagnostic += `Ensure all quotes inside code strings are escaped as \\", remove any unescaped control characters, and output complete valid JSON.`;

                try {
                    await submitPrompt(diagnostic);
                } finally {
                    isOperationInProgress = false;
                    updateApplyButtonUI();
                }
            } else if (patchRetryCount >= MAX_PATCH_RETRIES) {
                showToast('JSON repair aborted: check DevTools console (F12) for details', true);
            }
            return;
        }

        if (payload.inspect_files && payload.inspect_files.length > 0) {
            await handleAutoInspect(payload.inspect_files);
            return;
        }

        if (payload.changes && payload.changes.length > 0) {
            await sendChangesToServer(payload, btnElement, isAuto);
            return;
        }

        if (payload.message_to_user) {
            if (!isAuto) showToast(`Agent: ${payload.message_to_user}`);
        } else {
            if (!isAuto) showToast('No changes or file requests found', true);
        }
    }

    function checkGenerationStatus() {
        const generating = isAiGenerating();

        const turns = document.querySelectorAll('ms-chat-turn, ms-model-turn, .model-turn, [data-turn-role="model"]');
        const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;
        const curText = lastTurn ? (lastTurn.textContent || '') : '';
        const curLen = curText.length;

        if (generating && detectRepetitionLoop(curText)) {
            const stopBtn = findStopButton();
            if (stopBtn) {
                stopBtn.click();
                showToast('Watchdog: Loop detected! Generation aborted.', true);
            }
        }

        renderInChatMessageCards();

        if (curLen > 0 && curLen === lastModelTextLength) {
            textStableCounter++;
        } else {
            textStableCounter = 0;
            lastModelTextLength = curLen;
        }

        const isStableAndDone = !generating && textStableCounter >= 2 && curLen > 0;

        if (isStableAndDone && !isOperationInProgress) {
            updateApplyButtonUI();

            if (isQueued) {
                isQueued = false;
                updateApplyButtonUI();
                showToast('Generation complete. Applying queued changes...');
                handleApplyClick(null, false);
                wasGenerating = generating;
                return;
            }

            if (!isAutoApply || !isServerOnline) {
                wasGenerating = generating;
                return;
            }

            const payload = extractLatestJsonPayload(true);
            if (payload) {
                if (payload.error === 'JSON_SYNTAX_ERROR' && isAgentActive) {
                    handleApplyClick(null, true);
                } else if (payload.inspect_files && payload.inspect_files.length > 0 && isAgentActive) {
                    const inspectSig = JSON.stringify(payload.inspect_files);
                    if (inspectSig !== lastHandledInspectSig) {
                        handleAutoInspect(payload.inspect_files);
                    }
                } else if (payload.changes && payload.changes.length > 0) {
                    const changesSig = getChangesSignature(payload.changes);
                    if (changesSig !== lastAppliedSignature) {
                        showToast('Auto-applying changes...');
                        handleApplyClick(null, true);
                    }
                }
            }
        }
        wasGenerating = generating;
    }

    function openMultiFilePicker() {
        if (document.getElementById('studio-file-modal')) return;

        const overlay = createEl('div', {
            position: 'fixed', top: '0', left: '0', width: '100vw', height: '100vh',
            background: 'rgba(0,0,0,0.65)', zIndex: '2147483647',
            display: 'flex', justifyContent: 'center', alignItems: 'center'
        });
        overlay.id = 'studio-file-modal';

        const modal = createEl('div', {
            background: '#21252b', border: '1px solid #3b4048', borderRadius: '10px',
            width: '540px', maxHeight: '80vh', padding: '20px', display: 'flex',
            flexDirection: 'column', boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
            color: '#abb2bf', fontFamily: 'sans-serif'
        });

        const header = createEl('div', {
            display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px'
        });
        header.appendChild(createEl('h3', { margin: '0', color: '#fff', fontSize: '15px' }, 'Attach Workspace Files to Prompt'));
        const closeBtn = createEl('button', { background: 'transparent', border: 'none', color: '#abb2bf', fontSize: '18px', cursor: 'pointer' }, '✕');
        closeBtn.onclick = () => overlay.remove();
        header.appendChild(closeBtn);

        const searchInput = createEl('input', {
            background: '#282c34', border: '1px solid #3b4048', color: '#fff',
            padding: '8px 12px', borderRadius: '6px', marginBottom: '10px', fontSize: '13px'
        });
        searchInput.placeholder = 'Search project files...';

        const fileListContainer = createEl('div', {
            overflowY: 'auto', flexGrow: '1', maxHeight: '45vh', display: 'flex',
            flexDirection: 'column', gap: '4px', marginBottom: '14px'
        });

        const footer = createEl('div', { display: 'flex', justifyContent: 'space-between', alignItems: 'center' });
        const countLabel = createEl('span', { fontSize: '12px', color: '#888' }, 'Selected: 0');
        const submitBtn = createEl('button', {
            background: '#1a73e8', color: '#fff', border: 'none', padding: '8px 16px',
            borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold'
        }, 'Insert Selected');

        footer.appendChild(countLabel);
        footer.appendChild(submitBtn);
        modal.appendChild(header);
        modal.appendChild(searchInput);
        modal.appendChild(fileListContainer);
        modal.appendChild(footer);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

        let selected = new Set();
        let allFiles = [];

        GM_xmlhttpRequest({
            method: 'GET',
            url: `${getServerUrl()}/api/files`,
            onload: (res) => {
                if (res.status !== 200) return;
                allFiles = JSON.parse(res.responseText);

                function render(list) {
                    safeClear(fileListContainer);
                    list.forEach(file => {
                        const row = createEl('label', {
                            display: 'flex', alignItems: 'center', gap: '8px',
                            padding: '6px 10px', background: '#282c34', borderRadius: '5px',
                            cursor: 'pointer', border: '1px solid #3b4048', fontSize: '13px'
                        });
                        const cb = createEl('input', { cursor: 'pointer' });
                        cb.type = 'checkbox';
                        cb.checked = selected.has(file);
                        cb.onchange = () => {
                            if (cb.checked) selected.add(file);
                            else selected.delete(file);
                            countLabel.textContent = `Selected: ${selected.size}`;
                        };
                        row.appendChild(cb);
                        row.appendChild(createEl('span', { color: '#fff', flexGrow: '1' }, file));
                        fileListContainer.appendChild(row);
                    });
                }

                render(allFiles);
                searchInput.oninput = () => {
                    const q = searchInput.value.toLowerCase();
                    render(allFiles.filter(f => f.toLowerCase().includes(q)));
                };
            }
        });

        submitBtn.onclick = async () => {
            if (selected.size === 0) return;
            submitBtn.textContent = 'Loading...';
            let combined = '';
            for (const file of selected) {
                const res = await new Promise(r => {
                    GM_xmlhttpRequest({
                        method: 'GET',
                        url: `${getServerUrl()}/api/file_content?path=${encodeURIComponent(file)}`,
                        onload: (response) => { try { r(JSON.parse(response.responseText)); } catch(e) { r({content: ''}); } },
                        onerror: () => r({content: '[Error loading file]'})
                    });
                });
                combined += `# === File: ${file} ===\n\`\`\`\n${res.content}\n\`\`\`\n\n`;
            }
            insertTextToPrompt(combined);
            overlay.remove();
            showToast(`Attached ${selected.size} file(s)`);
        };
    }

    async function waitForElement(selector, timeout = 2500) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            const el = document.querySelector(selector);
            if (el && el.offsetParent !== null) return el;
            await new Promise(r => setTimeout(r, 60));
        }
        return document.querySelector(selector);
    }

    async function setSystemInstructions(text, title = 'Studio Agent') {
        await ensureSettingsPanelOpen();
        await new Promise(r => setTimeout(r, 150));

        const findTextarea = () => {
            const list = Array.from(document.querySelectorAll(
                'textarea[aria-label*="System instructions" i], ' +
                'ms-system-instructions textarea, ' +
                'ms-system-instructions-panel textarea, ' +
                'ms-system-instruction-editor textarea, ' +
                '.cdk-overlay-container textarea'
            ));
            return list.find(el => el.offsetParent !== null && !el.closest('.monaco-editor') && el !== getPromptInputArea());
        };

        let textarea = findTextarea();

        if (!textarea) {
            const triggerBtn = document.querySelector(
                'ms-system-instructions-panel > button, ' +
                'ms-system-instructions-panel button, ' +
                'button[aria-label*="System instructions" i], ' +
                'button[data-test-system-instructions-card], ' +
                'button.system-instructions-card, ' +
                'ms-system-instructions-panel .subtitle'
            );

            if (triggerBtn) {
                triggerBtn.click();
                await waitForElement(
                    'textarea[aria-label*="System instructions" i], ms-system-instructions-panel textarea, ms-system-instructions textarea, ms-system-instruction-editor textarea',
                    2000
                );
                textarea = findTextarea();
            }
        }

        if (textarea) {
            const titleInput = document.querySelector(
                'ms-system-instructions input, ' +
                'ms-system-instructions-panel input'
            );
            if (titleInput && titleInput.offsetParent !== null && text) {
                titleInput.focus();
                setNativeValue(titleInput, title);
                await new Promise(r => setTimeout(r, 80));
            }

            textarea.focus();
            if (text) {
                document.execCommand('selectAll', false, null);
                if (!document.execCommand('insertText', false, text)) {
                    setNativeValue(textarea, text);
                } else {
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                }
            } else {
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, '');
                setNativeValue(textarea, '');
            }

            await new Promise(r => setTimeout(r, 200));

            const closeBtn = Array.from(document.querySelectorAll(
                'button[aria-label="Close panel"], button[aria-label="Close"], button[aria-label*="close" i]'
            )).find(b => {
                if (b.offsetParent === null) return false;
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                const cls = (b.className || '').toLowerCase();
                return !aria.includes('run settings') && 
                       !cls.includes('runsettings') && 
                       !aria.includes('user') && 
                       !aria.includes('account');
            });

            if (closeBtn) {
                closeBtn.click();
            } else {
                textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true }));
            }
            return true;
        }

        console.warn('[Agent] System instructions textarea not found');
        return false;
    }

    async function ensureSettingsPanelOpen() {
        if (!getPromptInputArea()) return false;
        let switchBtn = document.querySelector('button[role="switch"][aria-label="Structured outputs"]');
        if (switchBtn && switchBtn.offsetParent !== null) return true;

        let toggleBtn = document.querySelector(
            'button.runsettings-toggle-button, ' +
            'button[aria-label*="run settings" i], ' +
            'button[aria-label*="Toggle run settings panel" i]'
        );

        if (!toggleBtn || toggleBtn.offsetParent === null) {
            const candidateButtons = Array.from(document.querySelectorAll('button'));
            toggleBtn = candidateButtons.find(b => {
                if (b.offsetParent === null) return false;
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                const txt = (b.innerText || '').toLowerCase();
                const cls = (b.className || '').toLowerCase();

                if (aria.includes('user') || aria.includes('account') || txt.includes('user') || cls.includes('user')) {
                    return false;
                }

                const hasTuneIcon = Boolean(
                    b.querySelector('mat-icon[fonticon="tune"], mat-icon[data-mat-icon-name="tune"]') ||
                    (b.querySelector('.material-symbols-outlined') && b.textContent.trim().includes('tune'))
                );

                return cls.includes('runsettings-toggle-button') ||
                       hasTuneIcon ||
                       aria.includes('run settings') ||
                       aria.includes('model settings') ||
                       txt.includes('run settings') ||
                       txt.includes('model settings');
            });
        }

        if (toggleBtn) {
            toggleBtn.click();
            await waitForElement('button[role="switch"][aria-label="Structured outputs"]', 1500);
        }

        switchBtn = document.querySelector('button[role="switch"][aria-label="Structured outputs"]');
        return Boolean(switchBtn && switchBtn.offsetParent !== null);
    }

    async function setStructuredOutputs(enable, schemaString = '') {
        if (enable) {
            await ensureSettingsPanelOpen();
        }
        let switchBtn = document.querySelector('button[role="switch"][aria-label="Structured outputs"]');
        if (!switchBtn) return false;

        const isChecked = switchBtn.getAttribute('aria-checked') === 'true' ||
                          switchBtn.classList.contains('mdc-switch--selected');

        if (enable) {
            if (!isChecked) {
                switchBtn.click();
            } else if (!document.querySelector('textarea[placeholder*="Press Tab to use an example"], .monaco-editor textarea')) {
                const parent = switchBtn.closest('ms-property, .property-row, .setting-row') || switchBtn.parentElement;
                const editBtn = parent ? Array.from(parent.querySelectorAll('button')).find(b => b !== switchBtn && (b.innerText.trim().toLowerCase() === 'edit' || b.getAttribute('aria-label')?.toLowerCase().includes('edit'))) : null;
                if (editBtn) editBtn.click();
            }
            const monacoInput = await waitForElement('textarea[placeholder*="Press Tab to use an example"], .monaco-editor textarea', 2000);
            if (monacoInput) {
                monacoInput.focus();
                await new Promise(r => setTimeout(r, 100));
                document.execCommand('selectAll', false, null);
                if (!document.execCommand('insertText', false, schemaString)) {
                    setNativeValue(monacoInput, schemaString);
                }
            }
            await new Promise(r => setTimeout(r, 300));
            const saveBtn = Array.from(document.querySelectorAll('button')).find(b =>
                b.offsetParent !== null && b.innerText.trim() === 'Save'
            );
            if (saveBtn) saveBtn.click();
            return true;
        } else {
            if (isChecked) switchBtn.click();
            return true;
        }
    }

    function isStructuredOutputsActive() {
        const switchBtn = document.querySelector('button[role="switch"][aria-label="Structured outputs"]');
        return Boolean(switchBtn && (switchBtn.getAttribute('aria-checked') === 'true' || switchBtn.classList.contains('mdc-switch--selected')));
    }

    async function applyAgentSetup(enable, silent = false) {
        if (isSyncing) return;
        isSyncing = true;
        try {
            if (enable) {
                const res = await new Promise((resolve, reject) => {
                    GM_xmlhttpRequest({
                        method: 'GET',
                        url: `${getServerUrl()}/api/system_instruction`,
                        onload: (r) => resolve(JSON.parse(r.responseText)),
                        onerror: reject
                    });
                });

                const sysInstruction = res.system_instruction || '';
                const schemaString = JSON.stringify(res.schema || {}, null, 2);

                await setSystemInstructions(sysInstruction, `Agent (${activePresetInfo.name})`);
                await setStructuredOutputs(true, schemaString);
                lastSyncedPresetId = activePresetInfo.id;
                GM_setValue('last_synced_preset_id', lastSyncedPresetId);
                currentSessionSyncedKey = getSessionKey();
                if (!silent) {
                    showToast(`Agent ON [${activePresetInfo.name}]`);
                }
            } else {
                await setSystemInstructions('');
                await setStructuredOutputs(false);
                lastSyncedPresetId = '';
                GM_setValue('last_synced_preset_id', '');
                currentSessionSyncedKey = '';
                if (!silent) {
                    showToast('Chat Mode (Agent Disabled)');
                }
            }
        } catch (e) {
            if (!silent) showToast('Failed to apply Agent configuration', true);
            return false;
        } finally {
            isSyncing = false;
        }
        return true;
    }

    async function waitForEditorReady(timeout = 15000) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            const area = getPromptInputArea();
            if (area && area.offsetParent !== null) {
                return area;
            }
            await new Promise(r => setTimeout(r, 250));
        }
        return getPromptInputArea();
    }

    function scheduleAutoSync(reason = '') {
        if (!isAgentActive || isSyncing || isAiGenerating()) return;
        if (syncDebounceTimer) clearTimeout(syncDebounceTimer);
        syncDebounceTimer = setTimeout(async () => {
            if (!isAgentActive || isSyncing || isAiGenerating() || !isServerOnline) return;

            const targetKey = getSessionKey();
            const needsSync = (currentSessionSyncedKey !== targetKey) ||
                              (activePresetInfo.id !== lastSyncedPresetId) ||
                              reason === 'initial_load' ||
                              reason === 'preset_changed' ||
                              reason === 'handshake_ready';

            if (!needsSync) return;

            const ready = await waitForEditorReady(reason === 'initial_load' ? 15000 : 8000);
            if (!ready) return;

            if (!isAgentActive || isSyncing || isAiGenerating() || !isServerOnline) return;

            const ok = await applyAgentSetup(true, true);
            if (ok) {
                currentSessionSyncedKey = targetKey;
                showToast(`Auto-synced Agent [${activePresetInfo.name}]`);
            }
        }, reason === 'initial_load' ? 1200 : 600);
    }

    function makeDraggable(element, handle, onClickOnly = null) {
        let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
        let hasMoved = false;

        handle.onmousedown = dragMouseDown;

        function dragMouseDown(e) {
            if (e.button !== 0) return;
            if (['BUTTON', 'INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;

            e.preventDefault();
            hasMoved = false;
            pos3 = e.clientX;
            pos4 = e.clientY;
            document.onmouseup = closeDragElement;
            document.onmousemove = elementDrag;
        }

        function elementDrag(e) {
            e.preventDefault();
            const deltaX = pos3 - e.clientX;
            const deltaY = pos4 - e.clientY;

            if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) hasMoved = true;

            pos1 = deltaX;
            pos2 = deltaY;
            pos3 = e.clientX;
            pos4 = e.clientY;

            let newTop = element.offsetTop - pos2;
            let newLeft = element.offsetLeft - pos1;

            newTop = Math.max(10, Math.min(window.innerHeight - element.offsetHeight - 10, newTop));
            newLeft = Math.max(10, Math.min(window.innerWidth - element.offsetWidth - 10, newLeft));

            element.style.top = newTop + "px";
            element.style.left = newLeft + "px";
            element.style.bottom = "auto";
            element.style.right = "auto";
        }

        function closeDragElement() {
            document.onmouseup = null;
            document.onmousemove = null;
            if (hasMoved) {
                GM_setValue('toolbar_pos', { top: element.style.top, left: element.style.left });
            } else if (onClickOnly) {
                onClickOnly();
            }
        }
    }

    const baseBtnStyle = {
        border: '1px solid #30363d',
        borderRadius: '6px',
        fontWeight: '600',
        fontSize: '11.5px',
        cursor: 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '4px',
        whiteSpace: 'nowrap',
        flexShrink: '0',
        lineHeight: '1',
        height: '30px',
        padding: '0 10px',
        userSelect: 'none',
        transition: 'all 0.15s ease',
        background: '#21262d',
        color: '#c9d1d9'
    };

    function renderToolbarContents() {
        const toolbar = document.getElementById('studio-agent-toolbar');
        if (!toolbar) return;
        toolbar.onmousedown = null;
        safeClear(toolbar);

        const savedPos = GM_getValue('toolbar_pos', null);
        if (savedPos && savedPos.top && savedPos.left) {
            toolbar.style.top = savedPos.top;
            toolbar.style.left = savedPos.left;
            toolbar.style.bottom = 'auto';
            toolbar.style.right = 'auto';
        }

        if (isCollapsed) {
            toolbar.style.padding = '6px 12px';
            toolbar.style.cursor = 'move';

            const pill = createEl('div', {
                display: 'flex', alignItems: 'center', gap: '8px', userSelect: 'none', cursor: 'move', whiteSpace: 'nowrap'
            });
            const statusDot = createEl('div', {
                width: '8px', height: '8px', borderRadius: '50%', flexShrink: '0',
                background: isServerOnline ? '#3fb950' : '#f85149',
                boxShadow: isServerOnline ? '0 0 8px rgba(63, 185, 80, 0.7), 0 0 2px #3fb950' : 'none'
            });
            const label = createEl('span', { color: '#e6edf3', fontSize: '11.5px', fontWeight: '600', whiteSpace: 'nowrap' }, `Agent: ${activePresetInfo.name}`);
            pill.appendChild(statusDot);
            pill.appendChild(label);
            toolbar.appendChild(pill);

            makeDraggable(toolbar, pill, () => {
                isCollapsed = false;
                GM_setValue('toolbar_collapsed', false);
                renderToolbarContents();
            });
            return;
        }

        toolbar.style.padding = '5px 8px';
        toolbar.style.cursor = 'default';

        const dragHandle = createEl('div', {
            cursor: 'move', color: '#484f58', marginRight: '4px', fontSize: '12px', userSelect: 'none', flexShrink: '0'
        }, '⋮⋮');
        dragHandle.title = 'Drag to reposition';
        makeDraggable(toolbar, dragHandle);
        toolbar.appendChild(dragHandle);

        const statusDot = createEl('div', {
            width: '9px', height: '9px', borderRadius: '50%', flexShrink: '0',
            background: isServerOnline ? '#3fb950' : '#f85149',
            boxShadow: isServerOnline ? '0 0 8px rgba(63, 185, 80, 0.7), 0 0 2px #3fb950' : 'none',
            marginRight: '6px', cursor: 'pointer'
        });
        statusDot.title = isServerOnline ? `Online (Port :${currentPort})` : 'Offline (Click to change port)';
        statusDot.onclick = () => {
            const p = prompt("Enter Bridge Server Port:", currentPort);
            if (p && !isNaN(p)) {
                currentPort = parseInt(p, 10);
                GM_setValue('bridge_port', currentPort);
                manualReconnect();
            }
        };
        toolbar.appendChild(statusDot);

        if (isServerOnline) {
            if (needsUserscriptUpdate) {
                const btnUpdateScript = createEl('button', {
                    ...baseBtnStyle,
                    background: '#9e6a03',
                    color: '#fff',
                    borderColor: '#d29922',
                    boxShadow: '0 0 8px rgba(210, 153, 34, 0.6)'
                }, `Update Script (v${bridgeRequiredScriptVer})`);
                btnUpdateScript.title = `Userscript is outdated (v${SCRIPT_VERSION}). Click to update in Tampermonkey`;
                btnUpdateScript.onclick = () => window.open(`${getServerUrl()}/agent.user.js`, '_blank');
                toolbar.appendChild(btnUpdateScript);
            }

            const btnApply = createEl('button', {
                ...baseBtnStyle,
                background: '#1f6feb', color: '#fff', borderColor: 'rgba(240, 246, 252, 0.1)'
            }, isOperationInProgress ? 'Applying...' : isQueued ? 'Queued...' : 'Apply');
            btnApply.id = 'studio-btn-apply';
            btnApply.disabled = isOperationInProgress;
            btnApply.onmouseover = () => { if (!btnApply.disabled) btnApply.style.background = '#388bfd'; };
            btnApply.onmouseout = () => { if (!btnApply.disabled) btnApply.style.background = '#1f6feb'; };
            btnApply.onclick = () => handleApplyClick(btnApply, false);

            const autoLabel = createEl('label', {
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                fontSize: '11px',
                fontWeight: '600',
                color: isAutoApply ? '#3fb950' : '#8b949e',
                cursor: 'pointer',
                userSelect: 'none',
                padding: '0 4px',
                height: '30px'
            });
            autoLabel.title = 'Auto-apply: automatically apply changes when AI finishes generating';
            const autoCb = createEl('input', { cursor: 'pointer', margin: '0' });
            autoCb.type = 'checkbox';
            autoCb.checked = Boolean(isAutoApply);

            function setAutoApply(val) {
                isAutoApply = Boolean(val);
                GM_setValue('auto_apply', isAutoApply);
                autoCb.checked = isAutoApply;
                autoLabel.style.color = isAutoApply ? '#3fb950' : '#8b949e';
                if (!isAutoApply) {
                    isQueued = false;
                    patchRetryCount = 0;
                    updateApplyButtonUI();
                }
                showToast(isAutoApply ? 'Auto-Apply: ON' : 'Auto-Apply: OFF');
            }

            autoCb.onclick = (e) => {
                e.stopPropagation();
            };
            autoCb.onchange = () => {
                setAutoApply(autoCb.checked);
            };
            autoLabel.onclick = (e) => {
                if (e.target === autoCb) return;
                e.preventDefault();
                setAutoApply(!isAutoApply);
            };
            autoLabel.appendChild(autoCb);
            autoLabel.appendChild(createEl('span', {}, 'Auto'));

            const btnAgent = createEl('button', {
                ...baseBtnStyle,
                background: isAgentActive ? '#238636' : '#21262d',
                color: isAgentActive ? '#fff' : '#8b949e',
                borderColor: isAgentActive ? '#3fb950' : '#30363d',
                boxShadow: isAgentActive ? '0 0 8px rgba(63, 185, 80, 0.5), 0 0 2px #3fb950' : 'none'
            }, isAgentActive ? 'Agent: ON' : 'Agent: OFF');

            btnAgent.onmouseover = () => {
                btnAgent.style.background = isAgentActive ? '#2ea043' : '#30363d';
            };
            btnAgent.onmouseout = () => {
                btnAgent.style.background = isAgentActive ? '#238636' : '#21262d';
            };
            btnAgent.onclick = async () => {
                isAgentActive = !isAgentActive;
                GM_setValue('agent_active', isAgentActive);
                if (isAgentActive) {
                    currentSessionSyncedKey = '';
                }
                await applyAgentSetup(isAgentActive);
                renderToolbarContents();
            };

            const selPreset = createEl('select', {
                background: '#21262d',
                color: '#c9d1d9',
                border: '1px solid #30363d',
                borderRadius: '6px',
                height: '30px',
                padding: '0 8px',
                fontSize: '11.5px',
                fontWeight: '600',
                cursor: 'pointer',
                outline: 'none'
            });
            selPreset.title = 'Switch active agent preset';
            const presetKeys = Object.keys(availablePresets);
            if (presetKeys.length > 0) {
                presetKeys.forEach(pid => {
                    const opt = document.createElement('option');
                    opt.value = pid;
                    opt.textContent = availablePresets[pid].name || pid;
                    opt.selected = pid === activePresetInfo.id;
                    selPreset.appendChild(opt);
                });
            } else {
                const opt = document.createElement('option');
                opt.value = activePresetInfo.id;
                opt.textContent = activePresetInfo.name;
                selPreset.appendChild(opt);
            }
            selPreset.onchange = async () => {
                const newPid = selPreset.value;
                GM_xmlhttpRequest({
                    method: 'POST',
                    url: `${getServerUrl()}/api/set_preset`,
                    headers: { 'Content-Type': 'application/json' },
                    data: JSON.stringify({ preset: newPid }),
                    onload: async (res) => {
                        if (res.status === 200) {
                            activePresetInfo = { id: newPid, name: availablePresets[newPid]?.name || newPid };
                            showToast(`Preset: ${activePresetInfo.name}`);
                            if (isAgentActive) {
                                await applyAgentSetup(true);
                            }
                            renderToolbarContents();
                        }
                    }
                });
            };

            function styleSecondaryButton(b) {
                b.onmouseover = () => { b.style.background = '#30363d'; b.style.color = '#f0f6fc'; b.style.borderColor = '#8b949e'; };
                b.onmouseout = () => { b.style.background = '#21262d'; b.style.color = '#c9d1d9'; b.style.borderColor = '#30363d'; };
            }

            const btnSkeleton = createEl('button', { ...baseBtnStyle }, 'Arch');
            btnSkeleton.title = 'Insert architecture tree into prompt';
            styleSecondaryButton(btnSkeleton);
            btnSkeleton.onclick = () => {
                GM_xmlhttpRequest({
                    method: 'GET',
                    url: `${getServerUrl()}/api/project_skeleton`,
                    onload: res => {
                        const d = JSON.parse(res.responseText);
                        insertTextToPrompt(d.skeleton);
                        showToast('Workspace skeleton attached');
                    }
                });
            };

            const btnAttach = createEl('button', { ...baseBtnStyle }, 'Files');
            btnAttach.title = 'Select and attach project files';
            styleSecondaryButton(btnAttach);
            btnAttach.onclick = openMultiFilePicker;

            const btnDash = createEl('button', { ...baseBtnStyle }, 'Dashboard');
            styleSecondaryButton(btnDash);
            btnDash.onclick = () => window.open(getServerUrl(), '_blank');

            toolbar.appendChild(btnApply);
            toolbar.appendChild(autoLabel);
            toolbar.appendChild(btnAgent);
            toolbar.appendChild(selPreset);
            toolbar.appendChild(btnSkeleton);
            toolbar.appendChild(btnAttach);
            toolbar.appendChild(btnDash);
        } else {
            const btnReconnect = createEl('button', {
                ...baseBtnStyle,
                background: '#21262d', color: '#f0f6fc'
            }, 'Connect');
            btnReconnect.onclick = manualReconnect;
            toolbar.appendChild(btnReconnect);
        }

        const btnCollapse = createEl('button', {
            background: 'transparent', color: '#6e7681', border: 'none',
            fontSize: '13px', cursor: 'pointer', marginLeft: '2px', height: '30px'
        }, '—');
        btnCollapse.title = 'Minimize toolbar';
        btnCollapse.onclick = () => {
            isCollapsed = true;
            GM_setValue('toolbar_collapsed', true);
            renderToolbarContents();
        };
        toolbar.appendChild(btnCollapse);
    }

    function createToolbar() {
        if (document.getElementById('studio-agent-toolbar')) return;
        const toolbar = createEl('div', {
            position: 'fixed', bottom: '28px', right: '28px', zIndex: '2147483646',
            display: 'flex', alignItems: 'center', gap: '6px', background: '#161b22',
            borderRadius: '8px', border: '1px solid #30363d', boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            whiteSpace: 'nowrap', flexWrap: 'nowrap', width: 'max-content'
        });
        toolbar.id = 'studio-agent-toolbar';
        document.body.appendChild(toolbar);
        renderToolbarContents();
    }

    function checkHandshake() {
        GM_xmlhttpRequest({
            method: 'GET',
            url: `${getServerUrl()}/api/status?client_ver=${encodeURIComponent(SCRIPT_VERSION)}`,
            timeout: 800,
            onload: res => {
                if (res.status === 200) {
                    try {
                        const d = JSON.parse(res.responseText);
                        if (d.service === SERVICE_NAME) {
                            const prevOnline = isServerOnline;
                            isServerOnline = true;
                            failedAttempts = 0;
                            let needsRender = !prevOnline;

                            const newPresetsStr = JSON.stringify(d.presets || {});
                            if (JSON.stringify(availablePresets) !== newPresetsStr) {
                                availablePresets = d.presets || {};
                                needsRender = true;
                            }
                            const prevPresetId = activePresetInfo.id;
                            if (d.active_preset && (activePresetInfo.id !== d.active_preset.id || activePresetInfo.name !== d.active_preset.name)) {
                                activePresetInfo = { id: d.active_preset.id, name: d.active_preset.name };
                                needsRender = true;
                            }
                            if (isAgentActive && (prevPresetId !== activePresetInfo.id || activePresetInfo.id !== lastSyncedPresetId || currentSessionSyncedKey !== getSessionKey())) {
                                scheduleAutoSync(prevPresetId !== activePresetInfo.id ? 'preset_changed' : 'handshake_ready');
                            }
                            const reqVer = d.min_userscript_version || d.version || '1.0';
                            const prevNeedsUpdate = needsUserscriptUpdate;
                            needsUserscriptUpdate = isNewerVersion(reqVer, SCRIPT_VERSION);
                            bridgeRequiredScriptVer = reqVer;
                            if (needsUserscriptUpdate !== prevNeedsUpdate) {
                                needsRender = true;
                                if (needsUserscriptUpdate && !hasWarnedScriptOutdated) {
                                    hasWarnedScriptOutdated = true;
                                    showToast(`Extension outdated (v${SCRIPT_VERSION} < v${reqVer}). Click Update Script`, true);
                                }
                            }
                            if (d.has_update && d.latest_version) {
                                const tb = document.getElementById('studio-agent-toolbar');
                                const dashBtn = tb ? tb.querySelector('button[title*="Dashboard"], button:nth-last-child(2)') : null;
                                if (dashBtn && !dashBtn.textContent.includes('●')) {
                                    dashBtn.title = `Update available (v${d.latest_version}) - Open Dashboard`;
                                }
                            }
                            if (needsRender || !document.getElementById('studio-btn-apply')) {
                                renderToolbarContents();
                            }
                            return;
                        }
                    } catch (e) {}
                }
                handleDisconnect();
            },
            onerror: () => handleDisconnect()
        });
    }

    function handleDisconnect() {
        isServerOnline = false;
        failedAttempts++;
        renderToolbarContents();
    }

    function manualReconnect() {
        showToast('Connecting...');
        checkHandshake();
    }

    function hookNavigationEvents() {
        const pushState = history.pushState;
        const replaceState = history.replaceState;

        history.pushState = function() {
            pushState.apply(history, arguments);
            window.dispatchEvent(new Event('locationchange'));
        };
        history.replaceState = function() {
            replaceState.apply(history, arguments);
            window.dispatchEvent(new Event('locationchange'));
        };
        window.addEventListener('popstate', () => window.dispatchEvent(new Event('locationchange')));
        window.addEventListener('locationchange', () => {
            if (window.location.href !== lastObservedUrl) {
                lastObservedUrl = window.location.href;
                scheduleAutoSync('url_changed');
            }
        });
    }

    hookNavigationEvents();
    createToolbar();
    checkHandshake();

    if (isAgentActive) {
        scheduleAutoSync('initial_load');
    }

    setTimeout(() => {
        const initialPayload = extractLatestJsonPayload(false);
        if (initialPayload) {
            lastAutoProcessedSignature = JSON.stringify(initialPayload);
            if (initialPayload.changes) {
                lastAppliedSignature = getChangesSignature(initialPayload.changes);
            }
        }
    }, 600);
    setTimeout(() => {
        const initialPayload = extractLatestJsonPayload(false);
        if (initialPayload && initialPayload.changes && !lastAppliedSignature) {
            lastAppliedSignature = getChangesSignature(initialPayload.changes);
        }
    }, 2000);

    setInterval(checkHandshake, 3500);
    setInterval(checkGenerationStatus, 500);
    setInterval(() => {
        if (window.location.href !== lastObservedUrl) {
            lastObservedUrl = window.location.href;
            scheduleAutoSync('poll_url_changed');
        }
    }, 1500);
})();