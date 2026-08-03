const API_URL = "";
let currentUser = null;
let currentSessionId = null;
let activeChallenge = null;
let globalPendingChallenges = [];
let currentTTSAudio = null;

// --- OUTPUT SANITIZATION (XSS defense) ---
// Any message content — from the LLM, retrieved docs, or another user's stored
// history — is untrusted. Render markdown, then strip scripts / event handlers /
// javascript: URIs with DOMPurify before it ever touches innerHTML.
//
// S7-04: strip external image sources. A markdown image like
// ![](http://tracker/pixel.gif) becomes an <img> that silently phones home
// (tracking pixel / IP + timing leak) the moment it renders. Allow only inline
// data: images and same-origin/relative paths; drop any absolute external src.
if (window.DOMPurify && !window.__imgHookInstalled) {
    window.__imgHookInstalled = true;
    DOMPurify.addHook('afterSanitizeAttributes', function (node) {
        if (node.tagName === 'IMG') {
            const src = node.getAttribute('src') || '';
            if (/^https?:\/\//i.test(src) || src.startsWith('//')) {
                node.removeAttribute('src');
                node.removeAttribute('srcset');
            }
        }
    });
}

function renderMD(text) {
    const html = marked.parse(text || "");
    if (window.DOMPurify) {
        return DOMPurify.sanitize(html, { ADD_ATTR: ['target'], FORBID_TAGS: ['style'] });
    }
    // Fail closed: if the sanitizer didn't load, never inject raw HTML.
    return escapeHTML(text || "");
}

// Escape a plain string for safe insertion as text inside innerHTML.
function escapeHTML(text) {
    const d = document.createElement('div');
    d.textContent = (text == null) ? "" : String(text);
    return d.innerHTML;
}

// --- TELEMETRY (Productive Struggle — Contribution 2) ---
let lastAiResponseAt = null;     // timestamp when AI finished responding
let dwellLoggedForTurn = false;  // ensure one dwell sample per AI turn

function logBehavior(event, value) {
    if (!currentUser) return;
    try {
        fetch(`${API_URL}/api/v1/telemetry/behavior`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username,
                session_id: currentSessionId,
                event: event,
                value: value != null ? String(value) : null
            }),
            keepalive: true
        }).catch(() => {});
    } catch (e) { /* telemetry must never break the UI */ }
}

// Dwell time = seconds from AI finishing its message to the student's first keystroke.
function markAiResponded() {
    lastAiResponseAt = Date.now();
    dwellLoggedForTurn = false;
}

function captureDwellOnFirstKeystroke() {
    if (lastAiResponseAt && !dwellLoggedForTurn) {
        const dwellSec = (Date.now() - lastAiResponseAt) / 1000;
        logBehavior('dwell', dwellSec.toFixed(2));
        dwellLoggedForTurn = true;
    }
}

// --- 1. GLOBAL FUNCTIONS (So buttons can find them) ---
window.startTopic = function (text) {
    document.getElementById('userInput').value = text;
    sendMessage();
};

function addFeedbackButtons(container, originalQuery) {
    const bar = document.createElement('div');
    bar.className = 'feedback-bar';

    const actions = [
        { id: 'up', icon: '👍', label: '' },
        { id: 'down', icon: '👎', label: '' },
        { id: 'simplify', icon: '👶', label: 'Simplify' },
        { id: 'deep_dive', icon: '🤿', label: 'Deep Dive' }
    ];

    // Helper function to execute the API call
    async function sendFeedbackPayload(type, text = null) {
        const res = await fetch(`${API_URL}/api/v1/chat/feedback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username,
                session_id: currentSessionId,
                message_index: 0,
                feedback_type: type,
                original_query: originalQuery,
                feedback_text: text // Include the text!
            })
        });
        const data = await res.json();
        if (data.action === 'regenerate') {
            window.sendMessage(data.modified_query, true);
        }
    }

    actions.forEach(act => {
        const btn = document.createElement('button');
        btn.className = 'feedback-btn';
        btn.innerHTML = `${act.icon} ${act.label}`;

        btn.onclick = async () => {
            // Highlight the button
            container.querySelectorAll('.feedback-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // If it's a THUMBS DOWN, show the text box
            if (act.id === 'down') {
                // Telemetry: capture the dissatisfaction click IMMEDIATELY,
                // even if the student never submits a written reason.
                logBehavior('thumbs_down_click', originalQuery);
                // Prevent duplicate boxes if clicked multiple times
                if (container.querySelector('.feedback-text-box')) return;

                const box = document.createElement('div');
                box.className = 'feedback-text-box';
                box.style.marginTop = '15px';
                box.style.padding = '10px';
                box.style.background = 'rgba(0,0,0,0.2)';
                box.style.borderRadius = '8px';
                box.style.border = '1px solid #444';

                box.innerHTML = `
                <div style="font-size:0.85rem; color:#ccc; margin-bottom:5px;">What went wrong?</div>
                <textarea placeholder="e.g., Too complicated, incorrect code, confusing diagram..." rows="2" 
                            style="width:100%; box-sizing:border-box; padding:8px; border-radius:4px; border:1px solid #555; background:#1e1f20; color:#e3e3e3; font-family:inherit; outline:none; resize:vertical;"></textarea>
                <div style="display:flex; justify-content:flex-end; margin-top:8px;">
                    <button style="padding:6px 12px; background:var(--accent-color); color:#000; border:none; border-radius:4px; font-weight:600; cursor:pointer; font-size:0.85rem;">Submit Feedback</button>
                </div>
            `;
                container.appendChild(box);

                // Handle Submit Click
                const submitBtn = box.querySelector('button');
                const textarea = box.querySelector('textarea');

                submitBtn.onclick = async () => {
                    submitBtn.innerText = "Saving...";
                    await sendFeedbackPayload(act.id, textarea.value);
                    box.innerHTML = '<span style="color:#a8c7fa; font-size:0.85rem;">✅ Thank you! Your feedback helps me learn.</span>';
                    setTimeout(() => box.remove(), 3000); // Hide after 3 seconds
                };

            } else {
                // For Thumbs Up, Simplify, Deep Dive -> Send immediately
                await sendFeedbackPayload(act.id, null);
            }
        };

        bar.appendChild(btn);
    });

    container.appendChild(bar);
}

function addSpeakButton(container, rawText) {
    // Main chat: inject next to "Tutor" label; classroom: use feedback-bar / cr-speak-bar
    const header = container.querySelector('.msg-header');
    let targetBar;
    if (header) {
        if (header.querySelector('.speak-btn')) return;
        targetBar = header;
    } else {
        let bar = container.querySelector('.feedback-bar');
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'cr-speak-bar';
            container.appendChild(bar);
        }
        if (bar.querySelector('.speak-btn')) return;
        targetBar = bar;
    }

    const btn = document.createElement('button');
    btn.className = 'feedback-btn speak-btn';
    btn.title = 'Listen to this response';
    btn.setAttribute('aria-label', 'Read response aloud');
    btn.innerHTML = '🔊 Listen';

    btn.onclick = async () => {
        if (btn.dataset.playing === 'true') {
            if (currentTTSAudio) { currentTTSAudio.pause(); currentTTSAudio = null; }
            btn.dataset.playing = 'false';
            btn.innerHTML = '🔊 Listen';
            btn.classList.remove('active');
            return;
        }
        // Stop any other audio currently playing
        if (currentTTSAudio) { currentTTSAudio.pause(); currentTTSAudio = null; }
        document.querySelectorAll('.speak-btn[data-playing="true"]').forEach(b => {
            b.dataset.playing = 'false'; b.innerHTML = '🔊 Listen'; b.classList.remove('active');
        });

        btn.innerHTML = '🔊 <span class="speak-loading">Generating audio<span class="speak-dots"><span>.</span><span>.</span><span>.</span></span></span>';
        btn.disabled = true;
        try {
            const res = await fetch(`${API_URL}/api/v1/tts/speak`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: rawText, voice: 'nova' })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: 'TTS request failed' }));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const audioBlob = await res.blob();
            const audioUrl = URL.createObjectURL(audioBlob);
            const audio = new Audio(audioUrl);
            currentTTSAudio = audio;

            btn.innerHTML = '⏹ Stop';
            btn.disabled = false;
            btn.dataset.playing = 'true';
            btn.classList.add('active');
            audio.play();

            audio.onended = () => {
                btn.innerHTML = '🔊 Listen';
                btn.dataset.playing = 'false';
                btn.classList.remove('active');
                URL.revokeObjectURL(audioUrl);
                if (currentTTSAudio === audio) currentTTSAudio = null;
            };
            audio.onerror = () => {
                btn.innerHTML = '🔊 Listen';
                btn.disabled = false;
                btn.dataset.playing = 'false';
                btn.classList.remove('active');
                if (currentTTSAudio === audio) currentTTSAudio = null;
            };
        } catch (e) {
            btn.innerHTML = '🔊 Listen';
            btn.disabled = false;
            btn.dataset.playing = 'false';
            alert(`Could not load audio: ${e.message}`);
        }
    };
    targetBar.appendChild(btn);
}

function addProfilerButton(container, relativeUrl) {
    const btn = document.createElement('a');
    const cleanPath = relativeUrl.startsWith('/') ? relativeUrl.slice(1) : relativeUrl;
    btn.href = `${API_URL}/${cleanPath}`;
    btn.target = "_blank";
    btn.className = 'feedback-btn'; // Reuse feedback style
    btn.style.color = '#e67e22';
    btn.style.borderColor = '#e67e22';
    btn.innerHTML = '⚡ Profile';

    // Append to feedback bar if exists, else container
    const bar = container.querySelector('.feedback-bar');
    if (bar) bar.appendChild(btn);
    else container.appendChild(btn);
}

window.sendMessage = async function (overrideText = null, hidden = false) {
    const input = document.getElementById('userInput');
    const rawText = overrideText || input.value.trim();

    if (!rawText) return;

    // Guard message size (keep in sync with backend config.MAX_MESSAGE_CHARS).
    const MAX_MESSAGE_CHARS = 8000;
    if (rawText.length > MAX_MESSAGE_CHARS) {
        alert(`Your message is too long (${rawText.length.toLocaleString()} characters). ` +
              `Please keep it under ${MAX_MESSAGE_CHARS.toLocaleString()} characters.`);
        return;
    }

    // MCN behaviour telemetry: detect genuine help-seeking as a "struggle" signal.
    // Skip system-tagged messages (they all start with "[").
    if (!hidden && !rawText.startsWith('[')) {
        const HINT_RE = /\b(hint|stuck|i'?m lost|confused|too hard|just tell me|give me (the|a) (answer|solution|hint)|i don'?t (get|understand)|help me|explain .*(again|simpler))\b/i;
        if (HINT_RE.test(rawText)) {
            logBehavior('hint_request', rawText.slice(0, 80));
        }
    }

    // Hide Welcome Screen
    const welcome = document.getElementById('welcome-view');
    if (welcome) welcome.style.display = 'none';
    const initialSug = document.getElementById('initialSuggestions');
    if (initialSug) initialSug.style.display = 'none';

    // --- STRIP TAGS FOR UI DISPLAY ---
    let displayUserText = rawText;
    if (rawText.startsWith("[WARMUP_ANSWER]")) {
        displayUserText = rawText.replace("[WARMUP_ANSWER]", "").trim();
    } else if (rawText.startsWith("[SOLVE_CHALLENGE]")) {
        // [SOLVE_CHALLENGE] Arrays | int arr[5];
        const parts = rawText.replace("[SOLVE_CHALLENGE]", "").split("|");
        const topic = parts[0].trim();
        const code = parts.slice(1).join("|").trim();
        // Format it beautifully as a Markdown code block
        displayUserText = `Here is my answer for **${topic}**: \n\`\`\`c\n${code}\n\`\`\``;
    }

    // Only show bubble if NOT hidden
    if (!hidden) {
        const userDiv = document.createElement('div');
        userDiv.className = 'message user';
        
        // Parse it with Marked if it has a markdown block, otherwise use basic formatting
        let displayText = displayUserText;
        if (displayUserText.includes('```c')) {
            displayText = renderMD(displayUserText);
        } else {
            if (displayUserText.includes('{') || displayUserText.includes(';')) {
                displayText = `<pre><code class="language-c">${escapeHTML(displayUserText)}</code></pre>`;
            } else {
                displayText = escapeHTML(displayUserText).replace(/\n/g, '<br>');
            }
        }
        
        userDiv.innerHTML = `<div class="msg-sender">You</div>` + displayText;
        document.getElementById('messages').appendChild(userDiv);
        Prism.highlightAllUnder(userDiv);
    }
    // ------------------------------------------------

    // Clear Input
    input.value = '';
    input.style.height = 'auto';
    scrollToBottom();

    // 2. Add Bot Placeholder (Skeleton Loader)
    const botDiv = document.createElement('div');
    botDiv.className = 'message bot';
    const progId = `prog-${Date.now()}`;
    botDiv.innerHTML = `
    <div class="msg-sender bot">Tutor</div>
    <div id="text-${progId}"></div> 
    <div class="loading-container" id="${progId}">
        <div class="loading-status">
            <div class="spinner"></div>
            <span id="status-text-${progId}">Thinking...</span>
        </div>
        <div class="skeleton-line w-100"></div>
        <div class="skeleton-line w-80"></div>
        <div class="skeleton-line w-60"></div>
    </div>`;
    document.getElementById('messages').appendChild(botDiv);

    // 3. Prepare Payload
    let textToSend = rawText;
    if (activeChallenge) {
        textToSend = `[CONTEXT: Challenge "${activeChallenge}"]\n\n${rawText}`;
        activeChallenge = null;
    }

    try {
        const payload = {
            message: textToSend,
            user_role: currentUser.role,
            username: currentUser.username,
            session_id: currentSessionId
        };

        // Watchdog: if no bytes arrive for WATCHDOG_MS, abort the fetch so we can
        // resolve the UI instead of spinning forever on a hung/half-open stream.
        const controller = new AbortController();
        const WATCHDOG_MS = 100000;  // > server STREAM_STALL_TIMEOUT (90s) so the server's own terminal event wins first
        let watchdogTimer = null;
        function armWatchdog() {
            if (watchdogTimer) clearTimeout(watchdogTimer);
            watchdogTimer = setTimeout(() => {
                console.warn("Stream watchdog fired — aborting stalled response.");
                try { controller.abort(); } catch (_) {}
            }, WATCHDOG_MS);
        }
        function clearWatchdog() {
            if (watchdogTimer) { clearTimeout(watchdogTimer); watchdogTimer = null; }
        }

        const response = await fetch(`${API_URL}/api/v1/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal
        });
        armWatchdog();

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        // ── Typewriter Buffer ──────────────────────────────────
        // Characters arrive from SSE in bursts. We drip-feed them
        // at a steady pace so the UI feels smooth and readable.
        let fullMarkdown = "";          // all text received so far
        let renderedLength = 0;         // how much we've rendered
        let typewriterTimer = null;
        let streamDone = false;         // has the SSE stream closed?
        let completeEvent = null;       // stash the 'complete' event
        let finalized = false;          // has the response been finalized (spinner cleared)?

        const TICK_MS = 16;             // ~60fps render tick

        function renderNextChunk() {
            typewriterTimer = null;     // mark timer as consumed
            if (renderedLength < fullMarkdown.length) {
                // Adaptive chunk size: bigger backlog → faster catch-up
                const backlog = fullMarkdown.length - renderedLength;
                const chunkSize = backlog > 500 ? 30 : backlog > 200 ? 15 : backlog > 50 ? 8 : 4;
                
                renderedLength = Math.min(renderedLength + chunkSize, fullMarkdown.length);
                const textContainer = document.getElementById(`text-${progId}`);
                if (textContainer) {
                    textContainer.innerHTML = renderMD(fullMarkdown.slice(0, renderedLength));
                }
                scrollToBottom();
                typewriterTimer = setTimeout(renderNextChunk, TICK_MS);
            } else if (streamDone && completeEvent) {
                // Buffer fully drained AND stream is done → do final render
                finishRender(completeEvent);
                completeEvent = null;
            } else if (streamDone && !finalized) {
                // Stream closed without a terminal 'complete' but we drip-fed some
                // text — finalize gracefully so the spinner never lingers.
                finalizeIncomplete('stream closed without completion');
            }
            // else: buffer caught up — typewriterTimer is null,
            //       so startTypewriter() can restart when new tokens arrive
        }

        function startTypewriter() {
            if (typewriterTimer === null) {
                typewriterTimer = setTimeout(renderNextChunk, TICK_MS);
            }
        }

        // Final rich render after typewriter finishes
        function finishRender(data) {
            finalized = true;
            clearWatchdog();
            if (typewriterTimer) { clearTimeout(typewriterTimer); typewriterTimer = null; }
            if (data.data.session_id) currentSessionId = data.data.session_id;

            // --- Update Pending Challenges UI ---
            if (data.data.skipped_challenges !== undefined) {
                updatePendingChallengesUI(data.data.skipped_challenges);
            }

            botDiv.innerHTML = `<div class="msg-header"><div class="msg-sender bot">Tutor</div></div>` + renderMD(data.data.answer);
            displaySources(data.data.sources);


            renderDiagrams(botDiv);
            Prism.highlightAllUnder(botDiv);
            injectCopyButtons(botDiv);

            displaySuggestions(data.data.suggestions, botDiv);
            addFeedbackButtons(botDiv, rawText);
            addSpeakButton(botDiv, data.data.answer);
            loadChatHistory();

            // Telemetry: AI finished — start the dwell-time clock for the next reply
            markAiResponded();

            if (data.data.warmup_topic) {
                const topicSpan = document.getElementById('warmupTopicName');
                const modal = document.getElementById('warmupModal');
                const input = document.getElementById('warmupInput');
                
                if (topicSpan && modal && input) {
                    topicSpan.innerText = data.data.warmup_topic;
                    input.value = "";
                    modal.style.display = 'flex';
                    setTimeout(() => input.focus(), 50); 
                } else {
                    console.error("Warmup Modal HTML elements not found!");
                }
            }
            scrollToBottom();
        }

        // Graceful finalize when the stream ends/aborts WITHOUT a terminal 'complete'.
        // Renders whatever partial text arrived (or an error) and clears the spinner,
        // so the UI never stays "stuck on generating".
        function finalizeIncomplete(reason) {
            if (finalized) return;
            finalized = true;
            clearWatchdog();
            if (typewriterTimer) { clearTimeout(typewriterTimer); typewriterTimer = null; }
            const partial = (fullMarkdown || "").trim();
            const header = `<div class="msg-header"><div class="msg-sender bot">Tutor</div></div>`;
            if (partial) {
                botDiv.innerHTML = header + renderMD(partial) +
                    `<div style="color:#c98a00;font-size:0.85em;margin-top:8px;">⚠️ This response may be incomplete — please retry if it looks cut off.</div>`;
                renderDiagrams(botDiv);
                Prism.highlightAllUnder(botDiv);
                injectCopyButtons(botDiv);
                addFeedbackButtons(botDiv, rawText);
            } else {
                botDiv.innerHTML = header +
                    `<span style="color:#ff897d;">⚠️ Sorry — I couldn't finish that response. Please try again.</span>`;
            }
            markAiResponded();
            scrollToBottom();
        }

        // ── SSE Read Loop ──────────────────────────────────────
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            armWatchdog();  // bytes arrived → reset the stall timer

            const lines = decoder.decode(value, { stream: true }).split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));

                        // 0. ERROR → terminal: finalize gracefully, stop spinning
                        if (data.type === 'error') {
                            streamDone = true;
                            finalizeIncomplete(data.message || 'server error');
                            continue;
                        }
                        // 1. STATUS UPDATE
                        if (data.type === 'status') {
                            const statusText = document.getElementById(`status-text-${progId}`);
                            if (statusText) statusText.innerText = data.message;
                        }
                        // 2. TOKEN STREAM → buffer, don't render instantly
                        else if (data.type === 'token' || data.type === 'answer') {
                            // Hide skeleton on first token
                            const skeletonLines = document.querySelectorAll(`#${progId} .skeleton-line`);
                            skeletonLines.forEach(line => line.style.display = 'none');

                            fullMarkdown += data.text;
                            startTypewriter();  // kick off drip-feed if not running
                        }
                        // 3. COMPLETE → stash and let typewriter finish
                        else if (data.type === 'complete') {
                            completeEvent = data;
                            streamDone = true;
                            // If typewriter already caught up, finish immediately
                            if (renderedLength >= fullMarkdown.length) {
                                if (typewriterTimer) clearTimeout(typewriterTimer);
                                finishRender(data);
                                completeEvent = null;
                            }
                        }
                        // 4. PROFILER
                        else if (data.type === 'profiler_report') {
                            addProfilerButton(botDiv, data.url);
                        }
                    } catch (e) { console.warn(e); }
                }
            }
        }

        // Stream closed. Guarantee the UI resolves even if no terminal 'complete'
        // arrived: drain any buffered text, else finalize gracefully.
        streamDone = true;
        clearWatchdog();
        if (!finalized) {
            if (completeEvent) {
                finishRender(completeEvent);
                completeEvent = null;
            } else if (renderedLength >= (fullMarkdown || "").length) {
                // Nothing left to drip-feed — finalize now.
                finalizeIncomplete('stream closed without completion');
            }
            // else: typewriter is still draining buffered text; renderNextChunk will
            // call finalizeIncomplete once it empties (streamDone && !completeEvent).
        }

    } catch (e) {
        clearWatchdog();
        console.error(e);
        // Render partial content if we have any; otherwise show a clear error.
        if (!finalized) {
            if ((fullMarkdown || "").trim()) {
                finalizeIncomplete(e && e.name === 'AbortError' ? 'timed out' : 'connection failed');
            } else {
                botDiv.innerHTML = `<div class="msg-header"><div class="msg-sender bot">Tutor</div></div>` +
                    (e && e.name === 'AbortError'
                        ? "<span style='color:#ff897d;'>⚠️ The response timed out. Please try again.</span>"
                        : "<span style='color:#ff897d;'>⚠️ Connection failed. Please try again.</span>");
                finalized = true;
                markAiResponded();
            }
        }
    }
};

function updatePendingChallengesUI(challenges) {
    const btn = document.getElementById('pendingBtn');
    const badge = document.getElementById('pendingBadge');
    const countText = document.getElementById('pendingCountText');
    const list = document.getElementById('pendingDropdownList');
    
    if (!btn || !list) return;

    if (challenges && challenges.length > 0) {
        btn.style.display = 'inline-flex';
        badge.innerText = challenges.length;
        if (countText) countText.innerText = `${challenges.length}/5`;
        
        list.innerHTML = '';
        challenges.forEach(challenge => {
            // Handle both legacy strings and new dicts safely
            let topic = typeof challenge === 'string' ? challenge : challenge.topic;
            let question = typeof challenge === 'string' ? `Write a code snippet for ${topic}.` : challenge.question;
            
            // Make the text safe to pass into the onclick string without breaking HTML
            let safeQuestion = encodeURIComponent(question);

            const div = document.createElement('div');
            div.className = 'pending-item';
            div.innerHTML = `
                <span style="font-weight:600; font-size:0.9rem; color:var(--text-secondary);">${topic}</span>
                <button class="pending-item-btn" onclick="openSolveModal('${topic}', '${safeQuestion}')">Solve</button>
            `;
            list.appendChild(div);
        });
    } else {
        btn.style.display = 'none';
        document.getElementById('pendingDropdown').style.display = 'none';
    }
}

function togglePendingModal() {
    const modal = document.getElementById('pendingModal');
    modal.style.display = modal.style.display === 'flex' ? 'none' : 'flex';
}

function retryChallenge(topic) {
    togglePendingModal();
    // Send the hidden instruction tag
    window.sendMessage(`[RETRY_CHALLENGE] ${topic}`, true);
}

function submitWarmup() {
    const ans = document.getElementById('warmupInput').value.trim();
    if (!ans) return;
    document.getElementById('warmupModal').style.display = 'none';
    // Send to backend with the special tag so the grader catches it
    window.sendMessage(`[WARMUP_ANSWER] ${ans}`, false); 
}

function skipWarmup() {
    document.getElementById('warmupModal').style.display = 'none';
    // MCN behaviour telemetry: skipping a challenge is a "struggle" signal.
    logBehavior('skip_challenge', 'warmup');
    // Send hidden skip to backend so it doesn't clutter chat
    window.sendMessage(`[WARMUP_ANSWER] skip`, true);
}

// --- 2. SUGGESTION RENDERER (Crucial) ---
function displaySuggestions(suggestions, container) {
    if (!suggestions || suggestions.length === 0) return;

    const suggestionsDiv = document.createElement('div');
    suggestionsDiv.className = 'bot-suggestions';

    suggestions.forEach(text => {
        const btn = document.createElement('button');
        btn.className = 'suggestion-btn';

        if (text.toLowerCase().startsWith('challenge')) {
            btn.classList.add('challenge');
            btn.innerText = "💪 " + text.replace('Challenge:', '').trim();
            btn.onclick = function () {
                activeChallenge = text.replace('Challenge:', '').trim();
                window.sendMessage(`I accept the challenge: ${activeChallenge}`);
            };
        } else {
            btn.innerText = text;
            // Explicitly bind the click
            btn.addEventListener('click', function () {
                window.sendMessage(text);
            });
        }
        suggestionsDiv.appendChild(btn);
    });

    container.appendChild(suggestionsDiv);
}

// --- 3. OTHER UI FUNCTIONS ---
function displaySources(sources) {
    const list = document.getElementById('sourcesList');
    list.innerHTML = '';
    if (!sources || sources.length === 0) return;

    const unique = new Map();
    sources.forEach(s => unique.set(s.document_name, s));
    unique.forEach(s => {
        const div = document.createElement('div');
        div.className = 'source-card';
        div.innerHTML = `<div class="source-title">📄 ${escapeHTML(s.document_name)}</div><div class="source-content">${renderMD(s.chunk_text || "")}</div>`;
        list.appendChild(div);
    });
    Prism.highlightAllUnder(list);
}

async function renderDiagrams(container) {
    const blocks = container.querySelectorAll('code');
    for (const block of blocks) {
        // Check if it's explicitly mermaid class OR starts with graph/flowchart
        const txt = block.textContent.trim();
        const isMermaid = block.classList.contains('language-mermaid') ||
            txt.startsWith('graph ') ||
            txt.startsWith('flowchart ');

        if (isMermaid) {
            try {
                const id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
                const { svg } = await window.mermaid.render(id, txt);
                const div = document.createElement('div');
                div.innerHTML = window.DOMPurify
                    ? DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } })
                    : "";
                div.style.textAlign = 'center';

                // Replace the <pre> parent if it exists, otherwise just the <code>
                if (block.parentElement.tagName === 'PRE') {
                    block.parentElement.replaceWith(div);
                } else {
                    block.replaceWith(div);
                }
            } catch (e) {
                console.error("Mermaid Render Error:", e);
            }
        }
    }
}

function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px';
}

function toggleAuth(view) {
    document.getElementById('loginForm').classList.toggle('hidden', view === 'signup');
    document.getElementById('signupForm').classList.toggle('hidden', view !== 'signup');
    // Clear any lingering messages when switching forms
    ['loginError', 'signupError', 'err-name', 'err-email', 'err-username',
     'err-password', 'err-password2'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.innerText = ''; el.classList.remove('show'); el.style.display = 'none'; }
    });
    const s = document.getElementById('signupSuccess');
    if (s) s.style.display = 'none';
}

function handleKeyPress(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        window.sendMessage();
    }
}

function scrollToBottom() {
    const c = document.getElementById('messages');
    c.scrollTop = c.scrollHeight;
}

// --- 4. AUTH & LOAD ---
// --- Auth helpers ---------------------------------------------------------
function showError(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerText = msg;
    el.classList.add('show');
    el.style.display = 'block';
}
function clearError(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerText = '';
    el.classList.remove('show');
    el.style.display = 'none';
}
function setFieldState(inputId, errId, msg) {
    const inp = document.getElementById(inputId);
    if (msg) { if (inp) { inp.classList.add('invalid'); inp.classList.remove('valid'); } showError(errId, msg); }
    else { if (inp) { inp.classList.remove('invalid'); inp.classList.add('valid'); } clearError(errId); }
    return !msg;
}

// Client-side validators (mirror backend app/core/validators.py — UX only; the
// server re-validates authoritatively).
const AUTH_RE = {
    email: /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/,
    username: /^[A-Za-z0-9_]{3,30}$/,
};
function vName(v)  { return (v || '').trim().length >= 2 ? '' : 'Please enter your full name.'; }
function vEmail(v) { return AUTH_RE.email.test((v || '').trim()) ? '' : 'Please enter a valid email address.'; }
function vUser(v)  { return AUTH_RE.username.test((v || '').trim()) ? '' : 'Username must be 3–30 letters, numbers, or underscores.'; }
function vPass(v, user, email) {
    const p = v || '';
    if (p.length < 8) return 'Password must be at least 8 characters.';
    if (!/[a-z]/.test(p)) return 'Add a lowercase letter.';
    if (!/[A-Z]/.test(p)) return 'Add an uppercase letter.';
    if (!/\d/.test(p)) return 'Add a number.';
    if (user && p.toLowerCase().includes(user.toLowerCase())) return 'Password must not contain your username.';
    return '';
}

function passwordStrength(p) {
    p = p || '';
    let score = 0;
    if (p.length >= 8) score++;
    if (p.length >= 12) score++;
    if (/[a-z]/.test(p) && /[A-Z]/.test(p)) score++;
    if (/\d/.test(p)) score++;
    if (/[^A-Za-z0-9]/.test(p)) score++;
    return Math.min(score, 4); // 0..4
}

function updatePasswordUI() {
    const p = document.getElementById('signPass')?.value || '';
    // requirement checklist
    const reqs = { len: p.length >= 8, upper: /[A-Z]/.test(p), lower: /[a-z]/.test(p), digit: /\d/.test(p) };
    document.querySelectorAll('#pwReqs li').forEach(li => {
        li.classList.toggle('met', !!reqs[li.dataset.req]);
    });
    // strength bar
    const bar = document.getElementById('pwBar');
    const label = document.getElementById('pwStrength');
    if (!bar) return;
    if (!p) { bar.style.width = '0'; if (label) label.innerText = ''; return; }
    const s = passwordStrength(p);
    const map = [
        { w: '25%', c: '#ff8a80', t: 'Weak' },
        { w: '45%', c: '#fbbf24', t: 'Fair' },
        { w: '70%', c: '#a8c7fa', t: 'Good' },
        { w: '100%', c: '#4ade80', t: 'Strong' },
    ][Math.max(0, s - 1)];
    bar.style.width = map.w;
    bar.style.background = map.c;
    if (label) { label.innerText = 'Password strength: ' + map.t; label.style.color = map.c; }
}

async function performLogin() {
    clearError('loginError');
    const u = (document.getElementById('loginUser').value || '').trim();
    const p = document.getElementById('loginPass').value || '';
    if (!u || !p) { showError('loginError', 'Please enter your username and password.'); return; }
    try {
        const res = await fetch(`${API_URL}/api/v1/auth/login`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p })
        });
        const result = await res.json();
        if (res.ok) {
            currentUser = result.user;
            localStorage.setItem('c_tutor_user', JSON.stringify(currentUser));
            routeUser();
        } else {
            showError('loginError', result.detail || 'Incorrect username or password.');
        }
    } catch (e) { console.error(e); showError('loginError', 'Connection failed. Please try again.'); }
}

async function performSignup() {
    ['err-name', 'err-email', 'err-username', 'err-password', 'err-password2', 'signupError'].forEach(clearError);
    const name = (document.getElementById('signName')?.value || '').trim();
    const email = (document.getElementById('signEmail')?.value || '').trim();
    const username = (document.getElementById('signUser')?.value || '').trim();
    const password = document.getElementById('signPass')?.value || '';
    const password2 = document.getElementById('signPass2')?.value || '';

    // Client-side validation (server re-checks authoritatively)
    let ok = true;
    ok = setFieldState('signName', 'err-name', vName(name)) && ok;
    ok = setFieldState('signEmail', 'err-email', vEmail(email)) && ok;
    ok = setFieldState('signUser', 'err-username', vUser(username)) && ok;
    ok = setFieldState('signPass', 'err-password', vPass(password, username, email)) && ok;
    ok = setFieldState('signPass2', 'err-password2', password2 === password ? '' : 'Passwords do not match.') && ok;
    if (!ok) return;

    const data = {
        name, email, username, password,
        university: document.getElementById('signUni')?.value || "",
        department: document.getElementById('signDept')?.value || "",
        interest: document.getElementById('signInterest')?.value || ""
    };
    const btn = document.querySelector('#signupForm .auth-btn');
    if (btn) { btn.disabled = true; btn.innerText = 'Creating account…'; }
    try {
        const res = await fetch(`${API_URL}/api/v1/auth/signup`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json().catch(() => ({}));
        if (res.ok) {
            const s = document.getElementById('signupSuccess');
            s.innerText = "✓ Account created! Redirecting to log in…";
            s.style.display = 'block';
            setTimeout(() => toggleAuth('login'), 1400);
        } else {
            // Map server field errors back onto the right field when possible
            const detail = result.detail || 'Signup failed. Please try again.';
            const dl = detail.toLowerCase();
            if (dl.includes('email')) setFieldState('signEmail', 'err-email', detail);
            else if (dl.includes('username')) setFieldState('signUser', 'err-username', detail);
            else if (dl.includes('password')) setFieldState('signPass', 'err-password', detail);
            else showError('signupError', detail);
        }
    } catch (e) {
        console.error(e);
        showError('signupError', "Connection failed. Please try again.");
    } finally {
        if (btn) { btn.disabled = false; btn.innerText = 'Create account'; }
    }
}

// Wire show/hide toggles, live password meter, and live field validation.
function initAuthUI() {
    document.querySelectorAll('.pw-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            const inp = document.getElementById(btn.dataset.target);
            if (!inp) return;
            const show = inp.type === 'password';
            inp.type = show ? 'text' : 'password';
            btn.innerText = show ? 'Hide' : 'Show';
            btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        });
    });
    const sp = document.getElementById('signPass');
    if (sp) sp.addEventListener('input', updatePasswordUI);
    // Live validation on blur for immediate, friendly feedback
    const live = [
        ['signName', 'err-name', v => vName(v)],
        ['signEmail', 'err-email', v => vEmail(v)],
        ['signUser', 'err-username', v => vUser(v)],
    ];
    live.forEach(([id, err, fn]) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('blur', () => { if (el.value) setFieldState(id, err, fn(el.value)); });
    });
    const p2 = document.getElementById('signPass2');
    if (p2) p2.addEventListener('input', () => {
        const p1 = document.getElementById('signPass')?.value || '';
        if (p2.value) setFieldState('signPass2', 'err-password2', p2.value === p1 ? '' : 'Passwords do not match.');
    });
}

function routeUser() {
    if (currentUser.role === 'teacher') window.location.href = 'teacher_dashboard.html';
    else initializeApp();
}

function initializeApp() {
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('app-screen').style.display = 'block';
    document.getElementById('welcomeName').innerText = currentUser.name || currentUser.username;
    document.getElementById('roleBadge').innerText = currentUser.role.toUpperCase();

    loadChatHistory();
    loadInitialPreferences();

    // Check for deep-link initial message from Dashboard
    const urlParams = new URLSearchParams(window.location.search);
    const initialMsg = urlParams.get('initial_msg');

    if (initialMsg) {
        // Clear UI manually to skip startNewChat() and avoid creating an extra session
        currentSessionId = null;
        document.getElementById('messages').innerHTML = '';
        document.getElementById('sourcesList').innerHTML = '';
        const welcome = document.getElementById('welcome-view');
        if (welcome) welcome.style.display = 'none';
        const initialSug = document.getElementById('initialSuggestions');
        if (initialSug) initialSug.style.display = 'none';
        document.querySelectorAll('.history-item').forEach(el => el.classList.remove('active'));

        if (initialMsg.startsWith('[SOLVE_PRELAB]')) {
            // Handoff from the classroom: solve a prelab problem in guided mode.
            const problem = initialMsg.replace('[SOLVE_PRELAB]', '').trim();

            // Show the problem as the user's message
            const userDiv = document.createElement('div');
            userDiv.className = 'message user';
            userDiv.innerHTML = `<div class="msg-sender">You</div>` + escapeHTML(problem);
            document.getElementById('messages').appendChild(userDiv);
            scrollToBottom();

            // Send the clean problem text → classifies as PROBLEM → guided complex-problem mode
            setTimeout(() => window.sendMessage(problem, true), 50);
        } else if (initialMsg.startsWith('[VERIFY_MASTERY]')) {
            // Handoff from the dashboard: earn mastery by taking the verify quiz.
            const concept = initialMsg.replace('[VERIFY_MASTERY]', '').trim();

            // Friendly user-facing message
            const userDiv = document.createElement('div');
            userDiv.className = 'message user';
            userDiv.innerHTML = `<div class="msg-sender">You</div>Verify my mastery of ${escapeHTML(concept)}`;
            document.getElementById('messages').appendChild(userDiv);
            scrollToBottom();

            // Send the exam trigger → runs the comprehensive 3-tier Mastery Exam
            setTimeout(() => window.sendMessage(`[MASTERY_EXAM] ${concept}`, true), 50);
        } else if (initialMsg.startsWith('[START_TOPIC]')) {
            // Extract concept and goal
            const match = initialMsg.match(/\[START_TOPIC\]\s+(.*?)\s+\[GOAL\]\s+(.*)/);
            if (match) {
                const concept = match[1];
                const goal = match[2];
                const fakeUserMsg = `I am ready to learn ${concept} to help me reach my goal of ${goal}. Where do we start?`;

                // Visually insert the user's question so the chat doesn't look empty
                const userDiv = document.createElement('div');
                userDiv.className = 'message user';
                userDiv.innerHTML = `<div class="msg-sender">You</div>` + escapeHTML(fakeUserMsg);
                document.getElementById('messages').appendChild(userDiv);
                scrollToBottom();
            }

            // Send hidden tag silently to trigger actual backend routing
            setTimeout(() => window.sendMessage(initialMsg, true), 50);
        } else {
            document.getElementById('userInput').value = initialMsg;
            document.getElementById('userInput').focus();
        }

        // Clear the URL parameter without reloading
        window.history.replaceState({}, document.title, window.location.pathname);
    } else {
        // Automatically greet the user ON LOGIN and trigger warm-up
        // --- FIX 1: Check if they already saw the warmup this session ---
        const hasSeenWarmup = sessionStorage.getItem('has_seen_warmup');
        
        if (!hasSeenWarmup) {
            // First time opening the chat this session
            sessionStorage.setItem('has_seen_warmup', 'true');
            startNewChat(true); // Triggers [INIT_SESSION] and the modal
        } else {
            // Just navigating back from the dashboard
            startNewChat(false); // Triggers [NEW_CHAT] (silent greeting)
        }
    }
}

function logout() { localStorage.removeItem('c_tutor_user'); location.reload(); }
function openDashboard() { window.location.href = currentUser.role === 'teacher' ? 'teacher_dashboard.html' : 'student_dashboard.html'; }

// --- 5. HISTORY & SIDEBAR ---
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    if (window.innerWidth <= 900) {
        // Mobile: slide in/out
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    } else {
        // Desktop: collapse width
        sidebar.classList.toggle('collapsed');
    }
}

function toggleResources() {
    if (window.innerWidth <= 900) {
        document.body.classList.toggle('show-resources-mobile');
    } else {
        document.body.classList.toggle('hide-resources');
    }
}


async function loadChatHistory() {
    if (!currentUser) return;
    try {
        const res = await fetch(`${API_URL}/api/v1/history/sessions?username=${currentUser.username}`);
        const sessions = await res.json();
        const list = document.getElementById('historyList');
        list.innerHTML = '';

        sessions.forEach(s => {
            const item = document.createElement('div');
            item.className = 'history-item';
            if (s.id === currentSessionId) item.classList.add('active');

            // Chat Title Text
            const span = document.createElement('span');
            span.innerText = s.title || "Untitled Chat";
            span.onclick = () => loadSession(s.id);

            // Delete Button (Trash Icon)
            const delBtn = document.createElement('button');
            delBtn.className = 'delete-chat-btn';
            delBtn.innerHTML = '🗑️'; // Or use SVG
            delBtn.onclick = (e) => {
                e.stopPropagation(); // Prevent clicking the chat itself
                deleteSession(s.id);
            };

            item.appendChild(span);
            item.appendChild(delBtn);
            list.appendChild(item);
        });
    } catch (e) { console.error("History load error", e); }
}

async function deleteSession(sessionId) {
    if (!confirm("Are you sure you want to remove this chat?")) return;

    try {
        const res = await fetch(`${API_URL}/api/v1/history/session/${sessionId}?username=${currentUser.username}`, {
            method: 'DELETE'
        });

        if (res.ok) {
            // If we deleted the active chat, reset to New Chat
            if (sessionId === currentSessionId) startNewChat();
            loadChatHistory(); // Refresh list
        }
    } catch (e) { console.error(e); }
}

async function loadSession(sessionId) {
    currentSessionId = sessionId;
    document.getElementById('welcome-view').style.display = 'none';
    document.getElementById('initialSuggestions').style.display = 'none';

    const msgDiv = document.getElementById('messages');
    msgDiv.innerHTML = '<div style="text-align:center; color:#666; margin-top:20px;">Loading...</div>';

    try {
        const res = await fetch(`${API_URL}/api/v1/history/session/${sessionId}?username=${currentUser.username}`);
        const sessionData = await res.json();

        msgDiv.innerHTML = '';

        sessionData.messages.forEach(msg => {
            const div = document.createElement('div');
            div.className = `message ${msg.role}`;
            if (msg.role === 'user') {
                // User content is untrusted — always escape before insertion.
                let text;
                if (msg.content.includes('{') || msg.content.includes(';')) {
                    text = `<pre><code class="language-c">${escapeHTML(msg.content)}</code></pre>`;
                } else {
                    text = escapeHTML(msg.content).replace(/\n/g, '<br>');
                }
                div.innerHTML = `<div class="msg-sender">You</div>` + text;
            } else {
                div.innerHTML = `<div class="msg-sender bot">Tutor</div>` + renderMD(msg.content);
                renderDiagrams(div);
            }
            msgDiv.appendChild(div);
        });

        if (sessionData.state && sessionData.state.skipped_challenges) {
            updatePendingChallengesUI(sessionData.state.skipped_challenges);
        } else {
            updatePendingChallengesUI([]);
        }
        Prism.highlightAllUnder(msgDiv);
        injectCopyButtons(msgDiv);
        scrollToBottom();
        loadChatHistory();
    } catch (e) { console.error("Session load error", e); }
}

// --- NEW: INJECT COPY BUTTONS INTO CODE BLOCKS ---
function injectCopyButtons(container) {
    const preBlocks = container.querySelectorAll('pre');
    preBlocks.forEach(pre => {
        // 1. Prevent duplicate buttons
        if (pre.querySelector('.copy-code-btn')) return;

        // 2. Ensure it's actually a code block (not a raw PRE)
        const codeBlock = pre.querySelector('code');
        if (!codeBlock) return;

        // 3. Create the button
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-code-btn';
        copyBtn.innerHTML = `
            <svg viewBox="0 0 24 24">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg> Copy
        `;

        // 4. Click Event to Copy Text
        copyBtn.onclick = () => {
            // Telemetry: log copy-code click (productive struggle metric)
            logBehavior('copy_code', codeBlock.innerText.length);
            // Copy to clipboard
            navigator.clipboard.writeText(codeBlock.innerText).then(() => {
                // Success UI Update
                const originalHtml = copyBtn.innerHTML;
                copyBtn.innerHTML = '✅ Copied!';
                copyBtn.style.color = '#34d399';
                copyBtn.style.borderColor = 'rgba(52, 211, 153, 0.3)';
                copyBtn.style.background = 'rgba(52, 211, 153, 0.1)';
                
                // Revert back after 2 seconds
                setTimeout(() => {
                    copyBtn.innerHTML = originalHtml;
                    copyBtn.style.color = '';
                    copyBtn.style.borderColor = '';
                    copyBtn.style.background = '';
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy text: ', err);
            });
        };

        pre.appendChild(copyBtn);
    });
}

async function startNewChat(isLogin = false) {
        currentSessionId = null;
        document.getElementById('messages').innerHTML = '';
        document.getElementById('sourcesList').innerHTML = '';

        document.getElementById('welcome-view').style.display = 'flex';
        document.getElementById('initialSuggestions').style.display = 'flex';

        document.querySelectorAll('.history-item').forEach(el => el.classList.remove('active'));

        // Decide which system tag to send
        const triggerMessage = isLogin ? '[INIT_SESSION]' : '[NEW_CHAT]';

        try {
            const res = await fetch(`${API_URL}/api/v1/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: triggerMessage, // <--- Sends the correct tag
                    user_role: currentUser.role,
                    username: currentUser.username,
                    session_id: null
                })
            });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const lines = decoder.decode(value, { stream: true }).split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));

                        if (data.type === 'complete') {
                            // Capture the session_id so subsequent messages continue THIS session
                            if (data.data && data.data.session_id) {
                                currentSessionId = data.data.session_id;
                            }

                            // 1. Inject the greeting text
                            let cleanGreeting = data.data.answer.replace("👋 **Welcome back!**\n\n", "").replace(/\n/g, '<br>');
                            document.getElementById('dynamicGreeting').innerHTML = cleanGreeting;

                            // 2. Inject the dynamic suggestion chips
                            const suggestionsDiv = document.getElementById('initialSuggestions');
                            suggestionsDiv.innerHTML = ''; // clear old ones

                            data.data.suggestions.forEach(text => {
                                const chip = document.createElement('div');
                                chip.className = 'suggestion-chip';
                                chip.innerText = text;
                                chip.style.pointerEvents = 'auto';
                                chip.addEventListener('click', function () {
                                    window.startTopic(text);
                                });
                                suggestionsDiv.appendChild(chip);
                            });

                            // =================================================
                            // 3. TRIGGER THE WARM-UP MODAL
                            // =================================================
                            if (data.data.warmup_topic) {
                                const topicSpan = document.getElementById('warmupTopicName');
                                const modal = document.getElementById('warmupModal');
                                const input = document.getElementById('warmupInput');
                                
                                if (topicSpan && modal && input) {
                                    topicSpan.innerText = data.data.warmup_topic;
                                    input.value = ""; // clear old input
                                    modal.style.display = 'flex';
                                    // Focus the input box so they can type immediately
                                    setTimeout(() => input.focus(), 50); 
                                }
                            }
                        }
                    } catch (e) { }
                }
            }
        }
    } catch (e) {
        console.error("Failed to load greeting", e);
    }
}

// --- CUSTOM INSTRUCTIONS / PREFERENCES LOGIC ---
let breakTimer = null;
function applyAccessibility(prefs) {
    // Toggle CSS classes on the body
    document.body.classList.toggle('a11y-dyslexia', prefs.dyslexia_font === true);
    document.body.classList.toggle('a11y-spacing', prefs.extra_spacing === true);
    document.body.classList.toggle('a11y-contrast', prefs.high_contrast === true);

    // Handle ADHD Break Timer (45 mins = 2700000 ms)
    if (breakTimer) clearTimeout(breakTimer);
    if (prefs.break_reminders === true) {
        breakTimer = setTimeout(() => {
            alert("🧘 Break Time! You've been crushing it for 45 minutes. Working memory needs time to process. Step away from the screen for 5 minutes and grab some water!");
        }, 2700000);
    }
}

async function openPreferencesModal() {
    document.getElementById('preferencesModal').style.display = 'flex';
    try {
        const res = await fetch(`${API_URL}/api/v1/user/preferences/${currentUser.username}`);
        const prefs = await res.json();
        
        document.getElementById('customInstText').value = prefs.custom_instructions || "";
        document.getElementById('chkExplanation').checked = prefs.show_explanation !== false;
        document.getElementById('chkUseCases').checked = prefs.show_use_cases !== false;
        document.getElementById('chkVisual').checked = prefs.show_visual_model !== false;
        document.getElementById('chkExample').checked = prefs.show_example_code !== false;
        
        // New A11y fields
        document.getElementById('chkLiteral').checked = prefs.literal_mode === true;
        document.getElementById('chkConcise').checked = prefs.concise_mode === true;
        document.getElementById('chkDyslexia').checked = prefs.dyslexia_font === true;
        document.getElementById('chkSpacing').checked = prefs.extra_spacing === true;
        document.getElementById('chkContrast').checked = prefs.high_contrast === true;
        document.getElementById('chkBreaks').checked = prefs.break_reminders === true;
    } catch (e) { console.error("Load failed", e); }
}

function closePreferencesModal() {
    document.getElementById('preferencesModal').style.display = 'none';
}

async function savePreferences() {
    const btn = document.getElementById('savePrefsBtn');
    const originalText = btn.innerText;
    btn.innerText = "Saving... ⏳";
    btn.style.opacity = "0.7";
    
    // Capture ALL preferences, including the new neurodiversity ones
    const prefs = {
        custom_instructions: document.getElementById('customInstText').value,
        show_explanation: document.getElementById('chkExplanation').checked,
        show_use_cases: document.getElementById('chkUseCases').checked,
        show_visual_model: document.getElementById('chkVisual').checked,
        show_example_code: document.getElementById('chkExample').checked,
        // New Accessibility & Pacing Settings
        literal_mode: document.getElementById('chkLiteral').checked,
        concise_mode: document.getElementById('chkConcise').checked,
        dyslexia_font: document.getElementById('chkDyslexia').checked,
        extra_spacing: document.getElementById('chkSpacing').checked,
        high_contrast: document.getElementById('chkContrast').checked,
        break_reminders: document.getElementById('chkBreaks').checked
    };
    
    const payload = {
        username: currentUser.username,
        preferences: prefs
    };
    
    try {
        await fetch(`${API_URL}/api/v1/user/preferences`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        btn.innerText = "✅ Saved!";
        btn.style.background = "#34d399"; // Green success
        
        // Instantly apply visual CSS changes (like Dyslexia font or Spacing)
        applyAccessibility(prefs); 
        
        setTimeout(() => {
            closePreferencesModal();
            // Reset button style
            btn.innerText = originalText;
            btn.style.background = "var(--accent-color)";
            btn.style.opacity = "1";
        }, 1000);
        
    } catch (e) {
        console.error("Failed to save preferences", e);
        btn.innerText = "❌ Error";
        setTimeout(() => { btn.innerText = originalText; btn.style.opacity = "1"; }, 2000);
    }
}

async function loadInitialPreferences() {
    if(!currentUser) return;
    const res = await fetch(`${API_URL}/api/v1/user/preferences/${currentUser.username}`);
    const prefs = await res.json();
    applyAccessibility(prefs);
}

// ═══════════════════════════════════════════
//  VIEW SWITCHING: Chat ↔ Classroom
// ═══════════════════════════════════════════
let currentView = 'chat'; // 'chat' or 'classroom'

function switchToChat() {
    currentView = 'chat';
    document.getElementById('chatView').style.display = 'flex';
    document.getElementById('classroomView').style.display = 'none';

    // Sources & Settings apply only to chat. Removing this class restores the panel
    // to whatever state it was in before entering the classroom (see chat.css).
    document.body.classList.remove('in-classroom');

    // Update nav buttons
    document.getElementById('navChat').classList.add('active');
    document.getElementById('navClassroom').classList.remove('active');
}

function switchToClassroom() {
    currentView = 'classroom';
    document.getElementById('chatView').style.display = 'none';
    document.getElementById('classroomView').style.display = 'flex';

    // In the classroom, RAG sources produce nothing (by design) and tutor settings do
    // not apply, so hide the Sources panel + Sources/Settings toolbar buttons via CSS.
    document.body.classList.add('in-classroom');

    // Update nav buttons
    document.getElementById('navChat').classList.remove('active');
    document.getElementById('navClassroom').classList.add('active');
    
    // Load video list if not already loaded
    if (!classroomVideosLoaded) {
        loadClassroomVideoList();
    }
}

// ═══════════════════════════════════════════
//  CLASSROOM (Embedded Video Chat)
// ═══════════════════════════════════════════
let classroomVideosLoaded = false;
let classroomVideoFilename = '';
let classroomTimestamp = 0;
let classroomIsPaused = false;
let classroomIsStreaming = false;
let classroomVideoData = []; // Cached video list for reference
let classroomChatMessages = [];
let classroomSessionId = null; // Persists across Q&A for the same video

// ── Classroom engagement / checkpoint / prelab state ──
let crCheckpoints = [];
let crAnswered = new Set();
let crActiveCheckpoint = null;
let crDuration = 0;
let crWatchedSec = 0;
let crLastTimeUpdate = null;
let crHiddenSec = 0;
let crTabHiddenAt = null;
let crMcqShownAt = null;
let crMcqAttempts = 0;
let crIntentionGiven = false;
let crPrelabProblems = [];
let crEndSequenceShown = false;
let crCoverageTimer = null;
let crCpSelected = null;
let crCpConfidence = 3;
let crPrelabChosen = null;

function crUser() { return currentUser ? currentUser.username : 'anonymous'; }

function crLogEngagement(event, position, detail) {
    if (!currentUser || !classroomVideoFilename) return;
    fetch('/api/v1/video/engagement', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
        body: JSON.stringify({ username: currentUser.username, video_filename: classroomVideoFilename,
            event: event, position_sec: position, detail: detail || null })
    }).catch(() => {});
}

function crPostCoverage() {
    if (!currentUser || !classroomVideoFilename || !crDuration) return;
    fetch('/api/v1/video/coverage', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
        body: JSON.stringify({ username: currentUser.username, video_filename: classroomVideoFilename,
            duration_sec: crDuration, watched_sec: crWatchedSec, hidden_sec: crHiddenSec })
    }).catch(() => {});
}

async function crLoadCheckpoints(filename) {
    try {
        const r = await fetch(`/api/v1/video/checkpoints/${encodeURIComponent(filename)}`);
        const d = await r.json();
        crCheckpoints = d.checkpoints || [];
        console.log(`[classroom] loaded ${crCheckpoints.length} checkpoints`);
    } catch (e) { console.error('checkpoint load failed', e); crCheckpoints = []; }
    try {
        const r2 = await fetch(`/api/v1/video/prelab/${encodeURIComponent(filename)}`);
        const d2 = await r2.json();
        crPrelabProblems = d2.problems || [];
    } catch (e) { crPrelabProblems = []; }
}

function crPendingCheckpointAt(time) {
    for (const cp of crCheckpoints) {
        if (cp.checkpoint_time <= time && !crAnswered.has(cp.checkpoint_time)) return cp;
    }
    return null;
}

function crEnforceCheckpoints() {
    if (crActiveCheckpoint) return;
    const videoEl = document.getElementById('classroomVideo');
    const cp = crPendingCheckpointAt(videoEl.currentTime);
    if (cp) { videoEl.pause(); crShowCheckpointModal(cp); }
}

// Topic → emoji mapping for cards
const TOPIC_ICONS = {
    'Variables': '📦',
    'Control Flow': '🔀',
    'Functions': '⚙️',
    'Arrays': '📊',
    'Strings': '💬',
    'Pointers': '📍',
    'Structures': '🏗️',
    'General': '📹',
};

// Topic → CSS class mapping
function topicClass(topic) {
    return 'topic-' + (topic || 'general').toLowerCase().replace(/\s+/g, '-');
}

function formatDuration(sec) {
    if (!sec || sec <= 0) return '';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

async function loadClassroomVideoList() {
    try {
        const resp = await fetch('/api/v1/video/list');
        const data = await resp.json();
        classroomVideoData = data.videos || [];
        
        const grid = document.getElementById('classroomVideoGrid');
        if (!grid) return;
        
        if (classroomVideoData.length === 0) {
            grid.innerHTML = `
                <div class="picker-empty">
                    <div class="picker-empty-icon">📭</div>
                    <p>No lecture videos available yet. Your teacher will add them soon!</p>
                </div>`;
            classroomVideosLoaded = true;
            return;
        }
        
        grid.innerHTML = '';
        
        for (const v of classroomVideoData) {
            const topic = v.topic || 'General';
            const icon = TOPIC_ICONS[topic] || '📹';
            const dur = formatDuration(v.duration_sec);
            const transcriptBadge = v.has_transcript ? '<span class="video-card-transcript-badge">✓ Ready</span>' : '';
            
            const card = document.createElement('button');
            card.className = 'video-card';
            card.setAttribute('aria-label', `Watch ${v.title}`);
            card.setAttribute('tabindex', '0');
            card.onclick = () => onClassroomVideoSelect(v.filename, v.title);
            
            card.innerHTML = `
                <div class="video-card-thumb" style="background: linear-gradient(135deg, rgba(110,157,245,0.06), rgba(167,139,250,0.06));">
                    <div class="video-card-thumb-bg">${icon}</div>
                    <div class="video-card-play-icon">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    </div>
                </div>
                <div class="video-card-body">
                    <span class="video-card-title">${escapeHtmlCr(v.title || v.filename)}</span>
                    <div class="video-card-meta">
                        <span class="video-card-topic ${topicClass(topic)}">${topic}</span>
                        ${dur ? `<span class="video-card-duration">⏱ ${dur}</span>` : ''}
                        <span class="video-card-size">${v.size_mb} MB</span>
                        ${transcriptBadge}
                    </div>
                </div>
            `;
            
            grid.appendChild(card);
        }
        
        classroomVideosLoaded = true;
    } catch (err) {
        console.error('Failed to load classroom videos:', err);
        const grid = document.getElementById('classroomVideoGrid');
        if (grid) grid.innerHTML = '<div class="picker-empty"><div class="picker-empty-icon">⚠️</div><p>Failed to load videos. Please try again.</p></div>';
    }
}

function onClassroomVideoSelect(filename, title) {
    if (!filename) return;
    
    classroomVideoFilename = filename;
    const videoEl = document.getElementById('classroomVideo');
    videoEl.src = `/videos/${encodeURIComponent(filename)}`;
    videoEl.load();

    // Reset state
    classroomTimestamp = 0;
    classroomIsPaused = false;
    classroomChatMessages = [];
    classroomSessionId = null; // New video = new session

    // Reset engagement / checkpoint state
    crCheckpoints = [];
    crAnswered = new Set();
    crActiveCheckpoint = null;
    crWatchedSec = 0;
    crHiddenSec = 0;
    crLastTimeUpdate = null;
    crIntentionGiven = false;
    crPrelabProblems = [];
    crEndSequenceShown = false;
    crDuration = 0;

    // Show player, hide picker
    document.getElementById('classroomPicker').style.display = 'none';
    document.getElementById('classroomPlayer').style.display = 'flex';

    // Set active title
    const displayTitle = title || filename.replace(/\.[^.]+$/, '').replace(/_/g, ' ');
    document.getElementById('classroomActiveTitle').textContent = displayTitle;

    const welcome = document.getElementById('classroomWelcome');
    if (welcome) welcome.style.display = 'flex';
    renderClassroomMessages();
    updateClassroomTimeBadge(0);
    updateClassroomTranscriptStatus('ready');

    // Bind video events
    videoEl.onpause = onClassroomPause;
    videoEl.onplay = onClassroomPlay;
    videoEl.ontimeupdate = onClassroomTimeUpdate;
    videoEl.onseeking = onClassroomSeeking;
    videoEl.onended = onClassroomEnded;
    videoEl.onvolumechange = onClassroomVolumeChange;
    videoEl.onloadeddata = () => { crDuration = videoEl.duration || 0; };

    // Attention + coverage flush
    if (!crCoverageTimer) crCoverageTimer = setInterval(crPostCoverage, 30000);
    document.addEventListener('visibilitychange', onClassroomVisibility);

    // Load checkpoints + prelab problems for this video
    crLoadCheckpoints(filename);

    // Load synced transcript panel
    loadTranscriptPanel(filename);
}

function backToVideoPicker() {
    // Pause any playing video
    const videoEl = document.getElementById('classroomVideo');
    if (videoEl && !videoEl.paused) videoEl.pause();
    
    // Show picker, hide player
    document.getElementById('classroomPicker').style.display = 'flex';
    document.getElementById('classroomPlayer').style.display = 'none';
    
    // Optionally reload the list (in case teacher changed visibility)
    loadClassroomVideoList();
}

function onClassroomPause() {
    classroomIsPaused = true;
    const videoEl = document.getElementById('classroomVideo');
    classroomTimestamp = videoEl.currentTime;
    updateClassroomTimeBadge(classroomTimestamp);

    const hint = document.getElementById('classroomPausedHint');
    if (hint) hint.classList.add('visible');

    if (!crActiveCheckpoint) crLogEngagement('pause', classroomTimestamp);
    crPostCoverage();

    setTimeout(() => document.getElementById('classroomChatInput').focus(), 100);
}

function onClassroomPlay() {
    const videoEl = document.getElementById('classroomVideo');
    // SRL gate: capture a learning intention before the first play
    if (!crIntentionGiven) {
        videoEl.pause();
        crShowIntentionModal();
        return;
    }
    // Block resuming while a checkpoint is pending
    if (crActiveCheckpoint) { videoEl.pause(); return; }

    classroomIsPaused = false;
    crLastTimeUpdate = videoEl.currentTime;
    const hint = document.getElementById('classroomPausedHint');
    if (hint) hint.classList.remove('visible');
    crLogEngagement('play', videoEl.currentTime);
}

function onClassroomTimeUpdate() {
    const videoEl = document.getElementById('classroomVideo');
    const currentTime = videoEl.currentTime;
    if (!classroomIsPaused) {
        classroomTimestamp = currentTime;
    }
    // Accumulate genuine watch time (ignore seek jumps)
    if (!videoEl.paused && crLastTimeUpdate !== null) {
        const dt = currentTime - crLastTimeUpdate;
        if (dt > 0 && dt < 1.5) crWatchedSec += dt;
    }
    crLastTimeUpdate = currentTime;

    // Enforce pending checkpoints reached by normal playback
    crEnforceCheckpoints();

    // Robust end-of-video trigger at ~97% (even if scrubbed)
    if (crDuration && currentTime >= crDuration * 0.97) crTriggerEndSequence();

    // Sync transcript highlight
    syncTranscriptHighlight(currentTime);
}

function onClassroomSeeking() {
    const videoEl = document.getElementById('classroomVideo');
    crLogEngagement('seek', videoEl.currentTime);
    crEnforceCheckpoints();  // force any un-answered checkpoint before the seek target
}

function onClassroomEnded() {
    const videoEl = document.getElementById('classroomVideo');
    crLogEngagement('ended', videoEl.currentTime);
    crPostCoverage();
    crTriggerEndSequence();
}

function onClassroomVolumeChange() {
    const videoEl = document.getElementById('classroomVideo');
    crLogEngagement(videoEl.muted || videoEl.volume === 0 ? 'mute' : 'unmute', videoEl.currentTime);
}

function onClassroomVisibility() {
    if (currentView !== 'classroom') return;
    const videoEl = document.getElementById('classroomVideo');
    if (document.hidden) {
        crTabHiddenAt = Date.now();
        if (videoEl && !videoEl.paused) crLogEngagement('tab_hidden', videoEl.currentTime, 'playing');
    } else {
        if (crTabHiddenAt) { crHiddenSec += (Date.now() - crTabHiddenAt) / 1000; crTabHiddenAt = null; }
        if (videoEl) crLogEngagement('tab_visible', videoEl.currentTime);
    }
}

function crTriggerEndSequence() {
    if (crEndSequenceShown || crActiveCheckpoint) return;
    const videoEl = document.getElementById('classroomVideo');
    const pending = crPendingCheckpointAt(crDuration || videoEl.duration || 1e9);
    if (pending) { videoEl.pause(); crShowCheckpointModal(pending); return; }
    crEndSequenceShown = true;
    crShowReflectionModal();
}

function updateClassroomTimeBadge(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    const str = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    const badge = document.getElementById('classroomTimeBadge');
    if (badge) badge.textContent = `0:00 – ${str}`;
}

// ═══ CLASSROOM CHECKPOINT MCQ (non-skippable) ═══
function crShowCheckpointModal(cp) {
    crActiveCheckpoint = cp;
    crMcqShownAt = Date.now();
    crMcqAttempts = 0;
    crCpSelected = null;
    crCpConfidence = 3;

    const body = document.getElementById('crCheckpointBody');
    const mins = Math.floor(cp.checkpoint_time / 60);
    const optsHtml = cp.options.map((opt, i) =>
        `<button class="cp-option" data-index="${i}" onclick="crSelectOption(${i})">
            <span class="cp-letter">${String.fromCharCode(65 + i)}</span> ${escapeHtmlCr(opt)}
         </button>`).join('');

    body.innerHTML = `
        <div class="cp-badge">⏸️ Checkpoint · ${mins} min</div>
        <p class="cp-instruction">Answer to continue — you can't skip this.</p>
        <div class="cp-confidence"><span>How confident are you?</span>
            <div class="cp-conf-btns">
                ${[1,2,3,4,5].map(n => `<button class="cp-conf" data-c="${n}" onclick="crSetConfidence(${n})">${n}</button>`).join('')}
            </div></div>
        <div class="cp-question">${escapeHtmlCr(cp.question)}</div>
        <div class="cp-options">${optsHtml}</div>
        <div class="cp-feedback" id="crCpFeedback"></div>
        <button class="cp-submit" id="crCpSubmit" onclick="crSubmitCheckpoint()" disabled>Submit Answer</button>`;
    document.getElementById('crCheckpointModal').classList.add('visible');
}

function crSelectOption(i) {
    crCpSelected = i;
    document.querySelectorAll('#crCheckpointModal .cp-option').forEach(b =>
        b.classList.toggle('selected', parseInt(b.dataset.index) === i));
    document.getElementById('crCpSubmit').disabled = false;
}

function crSetConfidence(n) {
    crCpConfidence = n;
    document.querySelectorAll('#crCheckpointModal .cp-conf').forEach(b =>
        b.classList.toggle('selected', parseInt(b.dataset.c) === n));
}

async function crSubmitCheckpoint() {
    if (crCpSelected === null || !crActiveCheckpoint) return;
    crMcqAttempts += 1;
    const submitBtn = document.getElementById('crCpSubmit');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Checking...';
    const tta = crMcqShownAt ? (Date.now() - crMcqShownAt) / 1000 : null;
    try {
        const resp = await fetch('/api/v1/video/checkpoint/answer', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: crUser(), video_filename: classroomVideoFilename,
                checkpoint_time: crActiveCheckpoint.checkpoint_time, selected_index: crCpSelected,
                confidence: crCpConfidence, time_to_answer_sec: tta, attempts: crMcqAttempts })
        });
        const data = await resp.json();
        const fb = document.getElementById('crCpFeedback');
        if (data.is_correct) {
            fb.innerHTML = `<div class="cp-correct">✅ Correct! ${escapeHtmlCr(data.explanation || '')}</div>`;
            crAnswered.add(crActiveCheckpoint.checkpoint_time);
            submitBtn.textContent = 'Continue ▶';
            submitBtn.disabled = false;
            submitBtn.onclick = crCloseCheckpointAndResume;
        } else {
            fb.innerHTML = `<div class="cp-wrong">❌ Not quite — review and try again.</div>`;
            submitBtn.textContent = 'Submit Answer';
            submitBtn.disabled = true;
            crCpSelected = null;
            document.querySelectorAll('#crCheckpointModal .cp-option').forEach(b => b.classList.remove('selected'));
        }
    } catch (e) {
        console.error('checkpoint submit failed', e);
        submitBtn.textContent = 'Submit Answer'; submitBtn.disabled = false;
    }
}

function crCloseCheckpointAndResume() {
    document.getElementById('crCheckpointModal').classList.remove('visible');
    crActiveCheckpoint = null;
    const videoEl = document.getElementById('classroomVideo');
    crLastTimeUpdate = videoEl.currentTime;
    const nearEnd = crDuration && videoEl.currentTime >= crDuration * 0.97;
    document.getElementById('crCpSubmit') && (document.getElementById('crCpSubmit').onclick = crSubmitCheckpoint);
    if (videoEl.ended || nearEnd) {
        crEndSequenceShown = true;
        crShowReflectionModal();
    } else {
        videoEl.play().catch(() => {});
    }
}

// ═══ CLASSROOM SRL: intention (pre) + reflection (post) ═══
function crShowIntentionModal() {
    const modal = document.getElementById('crIntentionModal');
    if (!modal) { crIntentionGiven = true; return; }
    modal.classList.add('visible');
    setTimeout(() => { const i = document.getElementById('crIntentionInput'); if (i) i.focus(); }, 100);
}

function crSubmitIntention() {
    const val = (document.getElementById('crIntentionInput').value || '').trim();
    if (currentUser && val) {
        fetch('/api/v1/video/reflection', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUser.username, video_filename: classroomVideoFilename,
                phase: 'intention', prompt: 'What do you want to learn from this video?', response: val }) }).catch(() => {});
    }
    crIntentionGiven = true;
    document.getElementById('crIntentionModal').classList.remove('visible');
    document.getElementById('classroomVideo').play().catch(() => {});
}

function crSkipIntention() {
    crIntentionGiven = true;
    document.getElementById('crIntentionModal').classList.remove('visible');
    document.getElementById('classroomVideo').play().catch(() => {});
}

function crShowReflectionModal() {
    const modal = document.getElementById('crReflectionModal');
    if (!modal) { crShowPrelabModal(); return; }
    modal.classList.add('visible');
    setTimeout(() => { const i = document.getElementById('crReflectionInput'); if (i) i.focus(); }, 100);
}

function crSubmitReflection() {
    const val = (document.getElementById('crReflectionInput').value || '').trim();
    if (currentUser && val) {
        fetch('/api/v1/video/reflection', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUser.username, video_filename: classroomVideoFilename,
                phase: 'reflection', prompt: 'What was your key takeaway?', response: val }) }).catch(() => {});
    }
    document.getElementById('crReflectionModal').classList.remove('visible');
    crShowPrelabModal();
}

// ═══ CLASSROOM END-OF-VIDEO COMPLEX PROBLEM (PRELAB) ═══
function crShowPrelabModal() {
    if (!crPrelabProblems || crPrelabProblems.length === 0) return;
    const modal = document.getElementById('crPrelabModal');
    const body = document.getElementById('crPrelabBody');
    if (!modal || !body) return;
    const idx = Math.floor(Math.random() * crPrelabProblems.length);
    crPrelabChosen = crPrelabProblems[idx];
    body.innerHTML = `
        <p class="cp-instruction">You've finished the lecture — ready to apply it? Try this challenge.</p>
        <div class="cp-question">${escapeHtmlCr(crPrelabChosen)}</div>
        <div class="prelab-actions">
            <button class="cp-skip" onclick="crDismissPrelab()">Maybe later</button>
            <button class="cp-submit-inline" onclick="crSolveInSage()">🚀 Solve in SAGE</button>
        </div>
        ${crPrelabProblems.length > 1 ? `<button class="prelab-shuffle" onclick="crShowPrelabModal()">↻ Show a different problem</button>` : ''}`;
    modal.classList.add('visible');
}

function crDismissPrelab() {
    document.getElementById('crPrelabModal').classList.remove('visible');
}

function crSolveInSage() {
    const problem = crPrelabChosen;
    if (!problem) return;
    if (currentUser) {
        fetch('/api/v1/video/prelab-start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
            body: JSON.stringify({ username: currentUser.username, video_filename: classroomVideoFilename, problem: problem }) }).catch(() => {});
    }
    // Switch to the chat view and send the problem as a guided complex problem
    document.getElementById('crPrelabModal').classList.remove('visible');
    switchToChat();
    const userDiv = document.createElement('div');
    userDiv.className = 'message user';
    userDiv.innerHTML = `<div class="msg-sender">You</div>` + problem;
    document.getElementById('messages').appendChild(userDiv);
    scrollToBottom();
    setTimeout(() => window.sendMessage(problem, true), 50);
}

// ═══ SYNCED TRANSCRIPT PANEL ═══

let classroomTranscriptSegments = []; // Raw segments from API
let transcriptAutoScroll = true;      // Auto-scroll follows playback
let lastActiveSegIdx = -1;            // Track last highlighted segment

async function loadTranscriptPanel(filename) {
    const container = document.getElementById('transcriptSegments');
    if (!container) return;
    
    container.innerHTML = '<div class="transcript-loading">Loading transcript…</div>';
    
    try {
        const resp = await fetch(`/api/v1/video/transcript/${encodeURIComponent(filename)}`);
        if (!resp.ok) throw new Error('Transcript not available');
        
        const data = await resp.json();
        classroomTranscriptSegments = data.segments || [];
        
        if (classroomTranscriptSegments.length === 0) {
            container.innerHTML = '<div class="transcript-loading">No transcript available for this video.</div>';
            return;
        }
        
        renderTranscriptSegments();
    } catch (err) {
        console.error('Failed to load transcript:', err);
        container.innerHTML = '<div class="transcript-loading">Transcript will appear after the video is first played.</div>';
    }
}

function renderTranscriptSegments() {
    const container = document.getElementById('transcriptSegments');
    if (!container) return;
    
    container.innerHTML = '';
    
    classroomTranscriptSegments.forEach((seg, idx) => {
        const row = document.createElement('div');
        row.className = 'transcript-segment';
        row.setAttribute('data-idx', idx);
        row.setAttribute('data-start', seg.start);
        row.setAttribute('data-end', seg.end);
        row.setAttribute('role', 'button');
        row.setAttribute('tabindex', '0');
        row.setAttribute('aria-label', `Jump to ${formatTimestamp(seg.start)}: ${seg.text}`);
        
        // Click to seek
        row.onclick = () => seekToTranscriptTime(seg.start);
        row.onkeydown = (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                seekToTranscriptTime(seg.start);
            }
        };
        
        row.innerHTML = `
            <span class="transcript-seg-time">${formatTimestamp(seg.start)}</span>
            <span class="transcript-seg-text">${escapeHtmlCr(seg.text)}</span>
        `;
        
        container.appendChild(row);
    });
    
    lastActiveSegIdx = -1;
}

function formatTimestamp(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

function syncTranscriptHighlight(currentTime) {
    if (classroomTranscriptSegments.length === 0) return;
    
    // Find the active segment
    let activeIdx = -1;
    for (let i = 0; i < classroomTranscriptSegments.length; i++) {
        const seg = classroomTranscriptSegments[i];
        if (currentTime >= seg.start && currentTime < seg.end) {
            activeIdx = i;
            break;
        }
    }
    
    // If between segments, find the last segment that started before currentTime
    if (activeIdx === -1) {
        for (let i = classroomTranscriptSegments.length - 1; i >= 0; i--) {
            if (currentTime >= classroomTranscriptSegments[i].start) {
                activeIdx = i;
                break;
            }
        }
    }
    
    // Skip if same segment as before (avoid DOM thrashing)
    if (activeIdx === lastActiveSegIdx) return;
    lastActiveSegIdx = activeIdx;
    
    const container = document.getElementById('transcriptSegments');
    if (!container) return;
    
    const rows = container.querySelectorAll('.transcript-segment');
    rows.forEach((row, idx) => {
        if (idx === activeIdx) {
            row.classList.add('active');
            // Auto-scroll: scroll the active segment into view
            if (transcriptAutoScroll) {
                row.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        } else {
            row.classList.remove('active');
        }
    });
}

function seekToTranscriptTime(seconds) {
    const videoEl = document.getElementById('classroomVideo');
    if (!videoEl) return;
    
    videoEl.currentTime = seconds;
    
    // If video is paused, update timestamp manually
    classroomTimestamp = seconds;
    updateClassroomTimeBadge(seconds);
    syncTranscriptHighlight(seconds);
}

function toggleTranscriptPanel() {
    const panel = document.getElementById('transcriptPanel');
    if (!panel) return;
    panel.classList.toggle('collapsed');
}

// Detect manual scroll in transcript to pause auto-scroll temporarily
(function() {
    let scrollTimeout;
    document.addEventListener('DOMContentLoaded', () => {
        // Telemetry: capture dwell time on the student's first keystroke after an AI reply
        const userInputEl = document.getElementById('userInput');
        if (userInputEl) {
            userInputEl.addEventListener('input', captureDwellOnFirstKeystroke);
        }

        const segContainer = document.getElementById('transcriptSegments');
        if (!segContainer) return;

        segContainer.addEventListener('scroll', () => {
            // User is manually scrolling — pause auto-scroll
            transcriptAutoScroll = false;
            clearTimeout(scrollTimeout);
            // Re-enable auto-scroll after 5s of no manual scrolling
            scrollTimeout = setTimeout(() => {
                transcriptAutoScroll = true;
            }, 5000);
        }, { passive: true });
    });
})();

function updateClassroomTranscriptStatus(state) {
    const el = document.getElementById('classroomTranscriptStatus');
    if (!el) return;
    switch (state) {
        case 'transcribing': el.textContent = '🎙️ Transcribing video...'; break;
        case 'ready': el.textContent = '✅ Ready to answer questions'; break;
        case 'error': el.textContent = '❌ Transcription failed'; break;
        default: el.textContent = '';
    }
}

function handleClassroomKeyPress(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendClassroomMessage();
    }
}

function autoResizeClassroom(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 100) + 'px';
}

async function sendClassroomMessage() {
    const inputEl = document.getElementById('classroomChatInput');
    const question = inputEl.value.trim();
    if (!question || classroomIsStreaming) return;
    
    if (!classroomVideoFilename) {
        classroomChatMessages.push({ role: 'system', content: 'Please select a video first.' });
        renderClassroomMessages();
        return;
    }
    
    // Hide welcome
    const welcome = document.getElementById('classroomWelcome');
    if (welcome) welcome.style.display = 'none';
    
    // Pause video if playing
    const videoEl = document.getElementById('classroomVideo');
    if (!videoEl.paused) videoEl.pause();
    
    const askTimestamp = videoEl.currentTime;
    
    // Add user message
    classroomChatMessages.push({ role: 'user', content: question, timestamp: askTimestamp });
    renderClassroomMessages();
    
    inputEl.value = '';
    inputEl.style.height = 'auto';
    
    classroomIsStreaming = true;
    document.getElementById('classroomSendBtn').disabled = true;
    
    const botMsg = { role: 'bot', content: '', timestamp: askTimestamp, streaming: true };
    classroomChatMessages.push(botMsg);
    renderClassroomMessages();
    
    updateClassroomTranscriptStatus('transcribing');
    
    try {
        const response = await fetch('/api/v1/video/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_filename: classroomVideoFilename,
                question: question,
                timestamp: askTimestamp,
            }),
        });
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const event = JSON.parse(line.slice(6));
                    if (event.type === 'token') {
                        botMsg.content += event.text;
                        renderClassroomMessages();
                    } else if (event.type === 'complete') {
                        botMsg.content = event.data.answer;
                        botMsg.streaming = false;
                        renderClassroomMessages();
                        updateClassroomTranscriptStatus('ready');
                        
                        // Save to chat history if we have a session
                        saveClassroomToHistory(question, botMsg.content, askTimestamp);
                    } else if (event.type === 'error') {
                        botMsg.content = `⚠️ Error: ${event.message}`;
                        botMsg.streaming = false;
                        renderClassroomMessages();
                        updateClassroomTranscriptStatus('error');
                    }
                } catch (parseErr) { /* ignore non-JSON */ }
            }
        }
    } catch (err) {
        console.error('Classroom chat error:', err);
        botMsg.content = '⚠️ Connection error. Please try again.';
        botMsg.streaming = false;
        renderClassroomMessages();
        updateClassroomTranscriptStatus('error');
    }
    
    classroomIsStreaming = false;
    document.getElementById('classroomSendBtn').disabled = false;
}

// Save classroom Q&A into the chat history system
async function saveClassroomToHistory(question, answer, timestamp) {
    if (!currentUser) return;

    const videoTitle = classroomVideoFilename.replace(/\.[^.]+$/, '').replace(/_/g, ' ');

    try {
        const res = await fetch(`${API_URL}/api/v1/history/save-classroom`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username,
                session_id: classroomSessionId,   // null on first Q, reused after
                video_title: videoTitle,
                question: question,
                answer: answer,
            })
        });
        const data = await res.json();
        classroomSessionId = data.session_id;  // store for next question
        loadChatHistory();
    } catch (err) {
        console.error('Failed to save classroom history:', err);
    }
}

function renderClassroomMessages() {
    const el = document.getElementById('classroomMessages');
    if (!el) return;
    
    if (classroomChatMessages.length === 0) {
        el.innerHTML = '';
        return;
    }
    
    let html = '';
    for (const msg of classroomChatMessages) {
        if (msg.role === 'system') {
            html += `<div class="cr-message" style="justify-content:center;"><div class="cr-message-content" style="text-align:center; font-size:0.8rem; color:var(--text-muted);">${escapeHtmlCr(msg.content)}</div></div>`;
            continue;
        }
        
        const isUser = msg.role === 'user';
        const avatarEmoji = isUser ? '🎓' : '🤖';
        const roleClass = isUser ? 'user' : 'bot';
        
        const mins = Math.floor((msg.timestamp || 0) / 60);
        const secs = Math.floor((msg.timestamp || 0) % 60);
        const timeStr = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        
        let contentHtml = isUser ? escapeHtmlCr(msg.content) : renderMarkdownCr(msg.content);
        
        let typingHtml = '';
        if (msg.streaming && !msg.content) {
            typingHtml = `<div class="cr-typing"><span></span><span></span><span></span></div>`;
        }
        
        const timeRefHtml = !isUser ? `<span class="cr-timestamp-ref">📍 Based on video up to ${timeStr}</span>` : '';
        
        html += `
            <div class="cr-message ${roleClass}">
                <div class="cr-message-avatar">${avatarEmoji}</div>
                <div class="cr-message-content">
                    ${timeRefHtml}
                    ${contentHtml}
                    ${typingHtml}
                </div>
            </div>`;
    }
    
    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;

    // Attach speak buttons to completed (non-streaming) bot messages only.
    // Guard prevents double-add; filter on !msg.streaming means buttons never appear mid-stream.
    const botMsgEls = el.querySelectorAll('.cr-message.bot');
    const completedBotMsgs = classroomChatMessages.filter(m => m.role === 'bot' && !m.streaming && m.content);
    botMsgEls.forEach((msgEl, i) => {
        const msg = completedBotMsgs[i];
        if (!msg) return;
        const contentDiv = msgEl.querySelector('.cr-message-content');
        if (!contentDiv || contentDiv.querySelector('.speak-btn')) return;
        addSpeakButton(contentDiv, msg.content);
    });
}

function escapeHtmlCr(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderMarkdownCr(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined') {
        marked.setOptions({ breaks: true, gfm: true });
        return renderMD(text);
    }
    return escapeHTML(text).replace(/\n/g, '<br>');
}

// Init
initAuthUI();
const stored = localStorage.getItem('c_tutor_user');
if (stored) { currentUser = JSON.parse(stored); routeUser(); }

let currentSolveTopic = "";

function togglePendingDropdown() {
    const dropdown = document.getElementById('pendingDropdown');
    dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
}

// Close dropdown if clicked outside
document.addEventListener('click', function(event) {
    const dropdown = document.getElementById('pendingDropdown');
    const btn = document.getElementById('pendingBtn');
    if (dropdown && btn && !dropdown.contains(event.target) && !btn.contains(event.target)) {
        dropdown.style.display = 'none';
    }
});

function updatePendingChallengesUI(challenges) {
    const btn = document.getElementById('pendingBtn');
    const badge = document.getElementById('pendingBadge');
    const countText = document.getElementById('pendingCountText');
    const list = document.getElementById('pendingDropdownList');
    
    if (!btn || !list) return;

    if (challenges && challenges.length > 0) {
        btn.style.display = 'inline-flex';
        badge.innerText = challenges.length;
        if (countText) countText.innerText = `${challenges.length}/5`;
        
        list.innerHTML = '';
        
        challenges.forEach(challenge => {
            // 1. Safely extract topic and question, handling both old and new data formats
            let topicStr = "Unknown Topic";
            let questionStr = "Write a code snippet for this topic.";

            if (typeof challenge === 'string') {
                topicStr = challenge;
            } else if (typeof challenge === 'object' && challenge !== null) {
                topicStr = challenge.topic || "Unknown Topic";
                questionStr = challenge.question || questionStr;
            }

            // 2. Create the wrapper div
            const div = document.createElement('div');
            div.className = 'pending-item';
            
            // 3. Create the text label
            const span = document.createElement('span');
            span.style.fontWeight = '600';
            span.style.fontSize = '0.9rem';
            span.style.color = 'var(--text-secondary)';
            span.innerText = topicStr; // Safe from HTML injection
            
            // 4. Create the button and attach the event listener directly
            const solveBtn = document.createElement('button');
            solveBtn.className = 'pending-item-btn';
            solveBtn.innerText = 'Solve';
            
            solveBtn.addEventListener('click', () => {
                openSolveModal(topicStr, questionStr);
            });
            
            // 5. Append everything to the list
            div.appendChild(span);
            div.appendChild(solveBtn);
            list.appendChild(div);
        });
    } else {
        btn.style.display = 'none';
        document.getElementById('pendingDropdown').style.display = 'none';
    }
}

function openSolveModal(topic, rawQuestion) {
    document.getElementById('pendingDropdown').style.display = 'none'; 
    currentSolveTopic = topic;
    document.getElementById('solveModalTopic').innerText = topic;
    
    // Render the question using Markdown safely
    const questionHtml = rawQuestion ? renderMD(rawQuestion) : "No question text available.";
    document.getElementById('solveModalQuestion').innerHTML = questionHtml;
    
    // Highlight any code blocks in the question
    Prism.highlightAllUnder(document.getElementById('solveModalQuestion'));
    
    document.getElementById('solveModalInput').value = '';
    document.getElementById('solveModal').style.display = 'flex';
    setTimeout(() => document.getElementById('solveModalInput').focus(), 100);
}

function closeSolveModal() {
    document.getElementById('solveModal').style.display = 'none';
    currentSolveTopic = "";
}

function submitSolveModal() {
    const code = document.getElementById('solveModalInput').value.trim();
    if (!code) return;
    
    const topic = currentSolveTopic;
    closeSolveModal();
    
    // Pass 'false' so it IS visible in the chat UI and logged to SQLite
    window.sendMessage(`[SOLVE_CHALLENGE] ${topic} | ${code}`, false);
}