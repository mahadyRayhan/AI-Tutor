const API_URL = "";
let currentUser = null;
let currentSessionId = null;
let activeChallenge = null;

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
    }

    // Only show bubble if NOT hidden
    if (!hidden) {
        const userDiv = document.createElement('div');
        userDiv.className = 'message user';
        let displayText = displayUserText.replace(/\n/g, '<br>');
        if (displayUserText.includes('{') || displayUserText.includes(';')) {
            displayText = `<pre><code class="language-c">${displayUserText.replace(/</g, '&lt;')}</code></pre>`;
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
        let currentMarkdown = "";

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
                        // 2. TOKEN STREAM
                        else if (data.type === 'token' || data.type === 'answer') {
                            // Hide the ghost lines, but KEEP the spinner alive
                            const skeletonLines = document.querySelectorAll(`#${progId} .skeleton-line`);
                            skeletonLines.forEach(line => line.style.display = 'none');

                            currentMarkdown += data.text;

                            // Render markdown into the dedicated text container
                            const textContainer = document.getElementById(`text-${progId}`);
                            if (textContainer) {
                                textContainer.innerHTML = marked.parse(currentMarkdown);
                            }
                            scrollToBottom();
                        }
                        // 3. COMPLETE
                        else if (data.type === 'complete') {
                            if (data.data.session_id) currentSessionId = data.data.session_id;

                            botDiv.innerHTML = `<div class="msg-sender bot">Tutor</div>` + marked.parse(data.data.answer);
                            displaySources(data.data.sources);
                            
                            renderDiagrams(botDiv);         
                            Prism.highlightAllUnder(botDiv); 
                            injectCopyButtons(botDiv);       
                            
                            displaySuggestions(data.data.suggestions, botDiv);
                            addFeedbackButtons(botDiv, rawText);
                            loadChatHistory();

                            if (data.data.warmup_topic) {
                                const topicSpan = document.getElementById('warmupTopicName');
                                const modal = document.getElementById('warmupModal');
                                const input = document.getElementById('warmupInput');
                                
                                if (topicSpan && modal && input) {
                                    topicSpan.innerText = data.data.warmup_topic;
                                    input.value = ""; // clear old input
                                    modal.style.display = 'flex';
                                    // Focus after a tiny delay to ensure display is complete
                                    setTimeout(() => input.focus(), 50); 
                                } else {
                                    console.error("Warmup Modal HTML elements not found!");
                                }
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
    } catch (e) {
        console.error(e);
        botDiv.innerHTML = "<span style='color:#ff897d'>Connection failed.</span>";
    }
};

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

    if (localStorage.getItem('sidebar_collapsed') === 'true' && window.innerWidth > 900) {
        document.getElementById('sidebar').classList.add('collapsed');
        document.querySelector('.menu-btn').textContent = '»';
    }

    if (localStorage.getItem('resources_collapsed') === 'true' && window.innerWidth > 900) {
        document.getElementById('resourcesPanel').classList.add('collapsed');
        document.getElementById('resCollapseBtn').textContent = '«';
    }

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
    const menuBtn = document.querySelector('.menu-btn');
    if (window.innerWidth <= 900) {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    } else {
        const isCollapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar_collapsed', isCollapsed);
        menuBtn.textContent = isCollapsed ? '»' : '☰';
    }
}

function toggleResources() {
    const panel = document.getElementById('resourcesPanel');
    const btn = document.getElementById('resCollapseBtn');
    if (window.innerWidth <= 900) {
        document.body.classList.toggle('show-resources-mobile');
    } else {
        const isCollapsed = panel.classList.toggle('collapsed');
        btn.textContent = isCollapsed ? '«' : '»';
        localStorage.setItem('resources_collapsed', isCollapsed);
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
let _prefModalOpener = null;
function applyAccessibility(prefs) {
    // Toggle CSS classes on the body
    document.body.classList.toggle('a11y-dyslexia', prefs.dyslexia_font === true);
    document.body.classList.toggle('a11y-spacing', prefs.extra_spacing === true);
    document.body.classList.toggle('a11y-contrast', prefs.high_contrast === true);
    document.body.classList.toggle('light-mode', prefs.light_mode === true);

    // Handle ADHD Break Timer (45 mins = 2700000 ms)
    if (breakTimer) clearTimeout(breakTimer);
    if (prefs.break_reminders === true) {
        breakTimer = setTimeout(() => {
            alert("🧘 Break Time! You've been crushing it for 45 minutes. Working memory needs time to process. Step away from the screen for 5 minutes and grab some water!");
        }, 2700000);
    }
}

async function openPreferencesModal() {
    _prefModalOpener = document.activeElement;
    try {
        const res = await fetch(`${API_URL}/api/v1/user/preferences/${currentUser.username}`, { cache: 'no-store' });
        const prefs = await res.json();
        document.getElementById('preferencesModal').style.display = 'flex';
        setTimeout(() => document.getElementById('customInstText').focus(), 50);
        
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
        document.getElementById('chkLightMode').checked = prefs.light_mode === true;
    } catch (e) { console.error("Load failed", e); }
}

function closePreferencesModal() {
    document.getElementById('preferencesModal').style.display = 'none';
    if (_prefModalOpener) { _prefModalOpener.focus(); _prefModalOpener = null; }
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
        break_reminders: document.getElementById('chkBreaks').checked,
        light_mode: document.getElementById('chkLightMode').checked
    };
    
    const payload = {
        username: currentUser.username,
        preferences: prefs
    };
    
    try {
        const saveRes = await fetch(`${API_URL}/api/v1/user/preferences`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!saveRes.ok) throw new Error(`Save failed: ${saveRes.status}`);
        btn.innerText = "✅ Saved!";
        btn.style.background = "#34d399"; // Green success

        // Instantly apply visual CSS changes (like Dyslexia font or Spacing)
        applyAccessibility(prefs);

        setTimeout(() => {
            // Reset button style (modal stays open)
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
    const res = await fetch(`${API_URL}/api/v1/user/preferences/${currentUser.username}`, { cache: 'no-store' });
    const prefs = await res.json();
    applyAccessibility(prefs);
}
// --- MODAL ACCESSIBILITY: ESC, click-outside, focus trap ---
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.getElementById('preferencesModal').style.display !== 'none') closePreferencesModal();
    else if (document.getElementById('warmupModal').style.display !== 'none') skipWarmup();
});

document.getElementById('preferencesModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closePreferencesModal();
});
document.getElementById('warmupModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) skipWarmup();
});

function _trapFocus(e) {
    if (e.key !== 'Tab') return;
    const focusable = Array.from(this.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
    )).filter(el => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus(); }
    } else {
        if (document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
}
document.getElementById('preferencesModal').addEventListener('keydown', _trapFocus);
document.getElementById('warmupModal').addEventListener('keydown', _trapFocus);

// Init
const stored = localStorage.getItem('c_tutor_user');
if (stored) { currentUser = JSON.parse(stored); routeUser(); }