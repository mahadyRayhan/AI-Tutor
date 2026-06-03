const API_URL = "";
let currentUser = null;
let currentSessionId = null;
let activeChallenge = null;
let globalPendingChallenges = [];
let currentTTSAudio = null;

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

        btn.innerHTML = '⏳ Loading...';
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
            displayText = marked.parse(displayUserText);
        } else {
            displayText = displayUserText.replace(/\n/g, '<br>');
            if (displayUserText.includes('{') || displayUserText.includes(';')) {
                displayText = `<pre><code class="language-c">${displayUserText.replace(/</g, '&lt;')}</code></pre>`;
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

        const response = await fetch(`${API_URL}/api/v1/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

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
                    textContainer.innerHTML = marked.parse(fullMarkdown.slice(0, renderedLength));
                }
                scrollToBottom();
                typewriterTimer = setTimeout(renderNextChunk, TICK_MS);
            } else if (streamDone && completeEvent) {
                // Buffer fully drained AND stream is done → do final render
                finishRender(completeEvent);
                completeEvent = null;
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
            if (data.data.session_id) currentSessionId = data.data.session_id;

            // --- Update Pending Challenges UI ---
            if (data.data.skipped_challenges !== undefined) {
                updatePendingChallengesUI(data.data.skipped_challenges);
            }

            botDiv.innerHTML = `<div class="msg-header"><div class="msg-sender bot">Tutor</div></div>` + marked.parse(data.data.answer);
            displaySources(data.data.sources);


            renderDiagrams(botDiv);
            Prism.highlightAllUnder(botDiv);
            injectCopyButtons(botDiv);

            displaySuggestions(data.data.suggestions, botDiv);
            addFeedbackButtons(botDiv, rawText);
            addSpeakButton(botDiv, data.data.answer);
            loadChatHistory();

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

        // ── SSE Read Loop ──────────────────────────────────────
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const lines = decoder.decode(value, { stream: true }).split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));

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

        // Safety: if stream ends without a 'complete' event, clean up typewriter
        streamDone = true;
        if (!completeEvent && typewriterTimer) {
            // Let the remaining buffer drain naturally
        }

    } catch (e) {
        console.error(e);
        botDiv.innerHTML = "<span style='color:#ff897d'>Connection failed.</span>";
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
        div.innerHTML = `<div class="source-title">📄 ${s.document_name}</div><div class="source-content">${marked.parse(s.chunk_text || "")}</div>`;
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
                div.innerHTML = svg;
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
async function performLogin() {
    const u = document.getElementById('loginUser').value;
    const p = document.getElementById('loginPass').value;
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
            document.getElementById('loginError').innerText = result.detail;
            document.getElementById('loginError').style.display = 'block';
        }
    } catch (e) { console.error(e); }
}

async function performSignup() {
    // Safe selection: use ?.value to prevent crash if element is missing
    const data = {
        name: document.getElementById('signName')?.value || "",
        email: document.getElementById('signEmail')?.value || "",
        username: document.getElementById('signUser')?.value || "",
        password: document.getElementById('signPass')?.value || "",
        // Optional fields with safety check
        university: document.getElementById('signUni')?.value || "",
        department: document.getElementById('signDept')?.value || "",
        interest: document.getElementById('signInterest')?.value || ""
    };

    // Validation
    if (!data.username || !data.password || !data.name) {
        showError('signupError', "Name, Username, and Password are required.");
        return;
    }

    try {
        const res = await fetch(`${API_URL}/api/v1/auth/signup`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (res.ok) {
            document.getElementById('signupSuccess').innerText = "Account created! Please login.";
            document.getElementById('signupSuccess').style.display = 'block';
            setTimeout(() => toggleAuth('login'), 1500);
        } else {
            const result = await res.json();
            showError('signupError', result.detail || "Signup failed");
        }
    } catch (e) {
        console.error(e);
        showError('signupError', "Connection failed");
    }
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

        if (initialMsg.startsWith('[START_TOPIC]')) {
            // Extract concept and goal
            const match = initialMsg.match(/\[START_TOPIC\]\s+(.*?)\s+\[GOAL\]\s+(.*)/);
            if (match) {
                const concept = match[1];
                const goal = match[2];
                const fakeUserMsg = `I am ready to learn ${concept} to help me reach my goal of ${goal}. Where do we start?`;

                // Visually insert the user's question so the chat doesn't look empty
                const userDiv = document.createElement('div');
                userDiv.className = 'message user';
                userDiv.innerHTML = `<div class="msg-sender">You</div>` + fakeUserMsg;
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
                let text = msg.content.replace(/\n/g, '<br>');
                if (text.includes('{') || text.includes(';')) text = `<pre><code class="language-c">${text.replace(/</g, '&lt;')}</code></pre>`;
                div.innerHTML = `<div class="msg-sender">You</div>` + text;
            } else {
                div.innerHTML = `<div class="msg-sender bot">Tutor</div>` + marked.parse(msg.content);
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
    
    // Update nav buttons
    document.getElementById('navChat').classList.add('active');
    document.getElementById('navClassroom').classList.remove('active');
}

function switchToClassroom() {
    currentView = 'classroom';
    document.getElementById('chatView').style.display = 'none';
    document.getElementById('classroomView').style.display = 'flex';
    
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
    
    setTimeout(() => document.getElementById('classroomChatInput').focus(), 100);
}

function onClassroomPlay() {
    classroomIsPaused = false;
    const hint = document.getElementById('classroomPausedHint');
    if (hint) hint.classList.remove('visible');
}

function onClassroomTimeUpdate() {
    const currentTime = document.getElementById('classroomVideo').currentTime;
    if (!classroomIsPaused) {
        classroomTimestamp = currentTime;
    }
    // Sync transcript highlight
    syncTranscriptHighlight(currentTime);
}

function updateClassroomTimeBadge(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    const str = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    const badge = document.getElementById('classroomTimeBadge');
    if (badge) badge.textContent = `0:00 – ${str}`;
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
        return marked.parse(text);
    }
    return text.replace(/\n/g, '<br>');
}

// Init
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
    const questionHtml = rawQuestion ? marked.parse(rawQuestion) : "No question text available.";
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