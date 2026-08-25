/**
 * Video Chat — Client-Side Logic
 * Handles: video events, SSE streaming, chat rendering
 */

// ═══════════════════════════════════
//  STATE
// ═══════════════════════════════════
let currentVideoFilename = '';
let currentTimestamp = 0;
let isPaused = false;
let isStreaming = false;
let chatMessages = [];

// User identity (for telemetry) — same store as the other pages
let currentUser = JSON.parse(localStorage.getItem('c_tutor_user') || 'null');

// ── Classroom engagement / checkpoint state ──
let checkpoints = [];               // [{checkpoint_time, question, options, concept}]
let answeredCheckpoints = new Set();// checkpoint_time values already answered
let activeCheckpoint = null;        // checkpoint currently shown in the modal
let videoDuration = 0;
let watchedSec = 0;                 // accumulated genuine watch time
let lastTimeUpdate = null;          // for coverage delta
let hiddenSec = 0;                  // time spent with tab hidden while playing
let tabHiddenAt = null;
let mcqShownAt = null;              // time-to-answer clock
let mcqAttempts = 0;
let intentionGiven = false;         // pre-video SRL intention captured
let coverageTimer = null;
let prelabProblems = [];            // complex problems for the end-of-video popup
let endSequenceShown = false;       // guard so the end reflection/prelab fires once

// DOM references (assigned in init)
let videoEl, videoSelect, messagesEl, inputEl, sendBtn;
let timeBadge, pausedHint, transcriptStatus, welcomeView;

// ── Telemetry helpers ──
function logVideoEngagement(event, position, detail) {
    if (!currentUser || !currentVideoFilename) return;
    fetch('/api/v1/video/engagement', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
        body: JSON.stringify({
            username: currentUser.username, video_filename: currentVideoFilename,
            event: event, position_sec: position, detail: detail || null
        })
    }).catch(() => {});
}

function postCoverage() {
    if (!currentUser || !currentVideoFilename || !videoDuration) return;
    fetch('/api/v1/video/coverage', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
        body: JSON.stringify({
            username: currentUser.username, video_filename: currentVideoFilename,
            duration_sec: videoDuration, watched_sec: watchedSec, hidden_sec: hiddenSec
        })
    }).catch(() => {});
}

// ═══════════════════════════════════
//  INITIALIZATION
// ═══════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    // Cache DOM elements
    videoEl          = document.getElementById('lectureVideo');
    videoSelect      = document.getElementById('videoSelect');
    messagesEl       = document.getElementById('chatMessages');
    inputEl          = document.getElementById('chatInput');
    sendBtn          = document.getElementById('sendBtn');
    timeBadge        = document.getElementById('timeBadge');
    pausedHint       = document.getElementById('pausedHint');
    transcriptStatus = document.getElementById('transcriptStatus');
    welcomeView      = document.getElementById('welcomeView');

    // Load video list
    loadVideoList();

    // Video event listeners
    videoEl.addEventListener('pause', onVideoPause);
    videoEl.addEventListener('play', onVideoPlay);
    videoEl.addEventListener('timeupdate', onTimeUpdate);
    videoEl.addEventListener('loadeddata', onVideoLoaded);
    videoEl.addEventListener('seeking', onVideoSeeking);
    videoEl.addEventListener('ended', onVideoEnded);
    videoEl.addEventListener('volumechange', onVolumeChange);

    // Attention: tab hidden while playing = likely background/inattentive
    document.addEventListener('visibilitychange', onVisibilityChange);

    // Persist coverage on unload
    window.addEventListener('beforeunload', postCoverage);
    // Periodic coverage flush
    coverageTimer = setInterval(postCoverage, 30000);

    // Input handling
    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    inputEl.addEventListener('input', autoResizeInput);
});


// ═══════════════════════════════════
//  VIDEO LIST & SELECTION
// ═══════════════════════════════════
async function loadVideoList() {
    try {
        // Prefer the chapter catalog: lectures belong to a numbered course structure,
        // so the picker groups them by chapter and orders them by video number rather
        // than listing raw filenames alphabetically.
        const catResp = await fetch('/api/v1/video/catalog?include_planned=false');
        if (catResp.ok) {
            const cat = await catResp.json();
            const chapters = (cat.chapters || []).filter(c => (c.videos || []).length);
            if (chapters.length) {
                renderCatalogOptions(chapters);
                return;
            }
        }
        await loadFlatVideoList();   // catalog empty / not seeded yet
    } catch (err) {
        console.error('Failed to load video catalog:', err);
        try { await loadFlatVideoList(); } catch (e) { /* already logged */ }
    }
}

function renderCatalogOptions(chapters) {
    videoSelect.innerHTML = '<option value="">— Select a lecture —</option>';
    let only = null, count = 0;

    for (const c of chapters) {
        const group = document.createElement('optgroup');
        group.label = `Chapter ${c.number}: ${c.title}`;
        for (const v of c.videos) {
            const opt = document.createElement('option');
            opt.value = v.filename;
            const num = v.video_number != null ? `Video ${v.video_number}: ` : '';
            opt.textContent = v.slides ? `${num}${v.title} (${v.slides})` : `${num}${v.title}`;
            group.appendChild(opt);
            only = v.filename; count++;
        }
        videoSelect.appendChild(group);
    }

    if (count === 1) {
        videoSelect.value = only;
        onVideoSelect(only);
    }
}

// Fallback for an unseeded catalog: the original flat, filename-derived list.
async function loadFlatVideoList() {
    const resp = await fetch('/api/v1/video/list');
    const data = await resp.json();

    videoSelect.innerHTML = '<option value="">— Select a lecture —</option>';

    for (const v of data.videos) {
        const opt = document.createElement('option');
        opt.value = v.filename;
        const displayName = v.filename
            .replace(/\.[^.]+$/, '')
            .replace(/_/g, ' ')
            .replace(/([a-z])([A-Z])/g, '$1 $2');
        opt.textContent = `${displayName} (${v.size_mb} MB)`;
        if (v.has_transcript) opt.textContent += ' ✓';
        videoSelect.appendChild(opt);
    }

    if (data.videos.length === 1) {
        videoSelect.value = data.videos[0].filename;
        onVideoSelect(data.videos[0].filename);
    }
}

function onVideoSelect(filename) {
    if (!filename) return;

    // Flush coverage for the previous video before switching
    postCoverage();

    currentVideoFilename = filename;
    videoEl.src = `/videos/${encodeURIComponent(filename)}`;
    videoEl.load();

    // Reset chat state
    currentTimestamp = 0;
    isPaused = false;
    chatMessages = [];

    // Reset engagement state
    checkpoints = [];
    answeredCheckpoints = new Set();
    activeCheckpoint = null;
    watchedSec = 0;
    hiddenSec = 0;
    lastTimeUpdate = null;
    intentionGiven = false;
    prelabProblems = [];
    endSequenceShown = false;

    // Show welcome view
    if (welcomeView) welcomeView.style.display = 'flex';
    renderMessages();
    updateTimeBadge(0);
    updateTranscriptStatus('ready');

    // Load checkpoint MCQs for this video
    loadCheckpoints(filename);
}

// ═══════════════════════════════════
//  CLASSROOM CHECKPOINTS + ENGAGEMENT
// ═══════════════════════════════════
async function loadCheckpoints(filename) {
    try {
        const resp = await fetch(`/api/v1/video/checkpoints/${encodeURIComponent(filename)}`);
        const data = await resp.json();
        checkpoints = data.checkpoints || [];
        console.log(`Loaded ${checkpoints.length} checkpoints`);
    } catch (e) {
        console.error('Failed to load checkpoints:', e);
        checkpoints = [];
    }
    // Also load the prelab complex problems for the end-of-video popup
    try {
        const r = await fetch(`/api/v1/video/prelab/${encodeURIComponent(filename)}`);
        const d = await r.json();
        prelabProblems = d.problems || [];
    } catch (e) {
        prelabProblems = [];
    }
}

// The earliest un-answered checkpoint at or before `time`, if any.
function pendingCheckpointAt(time) {
    for (const cp of checkpoints) {
        if (cp.checkpoint_time <= time && !answeredCheckpoints.has(cp.checkpoint_time)) {
            return cp;
        }
    }
    return null;
}

function enforceCheckpoints() {
    if (activeCheckpoint) return;                 // already showing one
    const cp = pendingCheckpointAt(videoEl.currentTime);
    if (cp) {
        videoEl.pause();
        showCheckpointModal(cp);
    }
}

function onVideoSeeking() {
    logVideoEngagement('seek', videoEl.currentTime);
    // If they seek past an un-answered checkpoint, force it before continuing
    enforceCheckpoints();
}

function onVideoEnded() {
    logVideoEngagement('ended', videoEl.currentTime);
    postCoverage();
    triggerEndSequence();
}

// Fires the end-of-video experience: any pending checkpoint first, then
// reflection → prelab. Robust to seeking/scrubbing (not just the 'ended' event).
function triggerEndSequence() {
    if (endSequenceShown || activeCheckpoint) return;
    // Force any still-pending checkpoint (e.g., the end-of-video one) first
    const pending = pendingCheckpointAt(videoDuration || videoEl.duration || 1e9);
    if (pending) {
        videoEl.pause();
        showCheckpointModal(pending);
        return;  // end sequence continues after this checkpoint is answered
    }
    endSequenceShown = true;
    showReflectionModal();  // → leads into the prelab complex problem
}

function onVolumeChange() {
    logVideoEngagement(videoEl.muted || videoEl.volume === 0 ? 'mute' : 'unmute',
                       videoEl.currentTime);
}

function onVisibilityChange() {
    if (document.hidden) {
        tabHiddenAt = Date.now();
        // Only interesting if the video is actually playing
        if (!videoEl.paused) logVideoEngagement('tab_hidden', videoEl.currentTime, 'playing');
    } else {
        if (tabHiddenAt) {
            hiddenSec += (Date.now() - tabHiddenAt) / 1000;
            tabHiddenAt = null;
        }
        logVideoEngagement('tab_visible', videoEl.currentTime);
    }
}


// ═══════════════════════════════════
//  VIDEO EVENT HANDLERS
// ═══════════════════════════════════
function onVideoPause() {
    isPaused = true;
    currentTimestamp = videoEl.currentTime;
    updateTimeBadge(currentTimestamp);

    // Show paused hint
    if (pausedHint) pausedHint.classList.add('visible');

    // Telemetry: only log genuine user pauses (not the checkpoint auto-pause)
    if (!activeCheckpoint) logVideoEngagement('pause', currentTimestamp);
    postCoverage();

    // Focus the input for quick typing
    setTimeout(() => inputEl.focus(), 100);
}

function onVideoPlay() {
    // SRL gate: capture a learning intention before the first play
    if (!intentionGiven) {
        videoEl.pause();
        showIntentionModal();
        return;
    }
    // Block resuming while a checkpoint is pending
    if (activeCheckpoint) {
        videoEl.pause();
        return;
    }
    isPaused = false;
    lastTimeUpdate = videoEl.currentTime;
    if (pausedHint) pausedHint.classList.remove('visible');
    logVideoEngagement('play', videoEl.currentTime);
}

function onTimeUpdate() {
    if (!isPaused) {
        currentTimestamp = videoEl.currentTime;
    }
    // Accumulate genuine watch time (ignore jumps from seeks)
    if (!videoEl.paused && lastTimeUpdate !== null) {
        const dt = videoEl.currentTime - lastTimeUpdate;
        if (dt > 0 && dt < 1.5) watchedSec += dt;
    }
    lastTimeUpdate = videoEl.currentTime;

    // Enforce any pending checkpoint reached by normal playback
    enforceCheckpoints();

    // Robust end-of-video trigger: fire once the student reaches ~97%,
    // even if they scrubbed to the end and the 'ended' event never fires.
    if (videoDuration && videoEl.currentTime >= videoDuration * 0.97) {
        triggerEndSequence();
    }
}

function onVideoLoaded() {
    updateTranscriptStatus('ready');
    videoDuration = videoEl.duration || 0;
}

function updateTimeBadge(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    const str = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    
    if (timeBadge) {
        timeBadge.textContent = `Context: 0:00 – ${str}`;
    }
}

function updateTranscriptStatus(state) {
    if (!transcriptStatus) return;
    
    switch (state) {
        case 'transcribing':
            transcriptStatus.textContent = '🎙️ Transcribing video...';
            break;
        case 'ready':
            transcriptStatus.textContent = '✅ Ready to answer questions';
            break;
        case 'error':
            transcriptStatus.textContent = '❌ Transcription failed';
            break;
        default:
            transcriptStatus.textContent = '';
    }
}


// ═══════════════════════════════════
//  CHAT — SEND & RECEIVE
// ═══════════════════════════════════
async function sendMessage() {
    const question = inputEl.value.trim();
    if (!question || isStreaming) return;
    
    if (!currentVideoFilename) {
        addSystemMessage('Please select a video first.');
        return;
    }
    
    // Hide welcome view
    if (welcomeView) welcomeView.style.display = 'none';
    
    // Pause video if playing
    if (!videoEl.paused) {
        videoEl.pause();
    }
    
    // Capture timestamp at the moment of asking
    const askTimestamp = videoEl.currentTime;
    
    // Add user message
    const userMsg = {
        role: 'user',
        content: question,
        timestamp: askTimestamp,
    };
    chatMessages.push(userMsg);
    renderMessages();
    
    // Clear input
    inputEl.value = '';
    inputEl.style.height = 'auto';
    
    // Start streaming
    isStreaming = true;
    sendBtn.disabled = true;
    
    // Add bot message placeholder
    const botMsg = {
        role: 'bot',
        content: '',
        timestamp: askTimestamp,
        streaming: true,
    };
    chatMessages.push(botMsg);
    renderMessages();
    
    updateTranscriptStatus('transcribing');
    
    try {
        const response = await fetch('/api/v1/video/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_filename: currentVideoFilename,
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
            
            // Process SSE events
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer
            
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                
                try {
                    const event = JSON.parse(line.slice(6));
                    
                    if (event.type === 'token') {
                        botMsg.content += event.text;
                        renderMessages();
                    } else if (event.type === 'complete') {
                        botMsg.content = event.data.answer;
                        botMsg.streaming = false;
                        renderMessages();
                        updateTranscriptStatus('ready');
                    } else if (event.type === 'error') {
                        botMsg.content = `⚠️ Error: ${event.message}`;
                        botMsg.streaming = false;
                        renderMessages();
                        updateTranscriptStatus('error');
                    }
                } catch (parseErr) {
                    // Ignore non-JSON lines
                }
            }
        }
        
    } catch (err) {
        console.error('Chat stream error:', err);
        botMsg.content = '⚠️ Connection error. Please try again.';
        botMsg.streaming = false;
        renderMessages();
        updateTranscriptStatus('error');
    }
    
    isStreaming = false;
    sendBtn.disabled = false;
}


// ═══════════════════════════════════
//  CHAT — RENDERING
// ═══════════════════════════════════
function renderMessages() {
    if (!messagesEl) return;
    
    if (chatMessages.length === 0) {
        messagesEl.innerHTML = '';
        return;
    }
    
    let html = '';
    
    for (const msg of chatMessages) {
        if (msg.role === 'system') {
            html += `<div class="message system"><div class="message-content">${escapeHtml(msg.content)}</div></div>`;
            continue;
        }
        
        const isUser = msg.role === 'user';
        const avatarEmoji = isUser ? '🎓' : '🤖';
        const roleClass = isUser ? 'user' : 'bot';
        
        // Format timestamp reference
        const mins = Math.floor(msg.timestamp / 60);
        const secs = Math.floor(msg.timestamp % 60);
        const timeStr = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        
        let contentHtml;
        if (isUser) {
            contentHtml = escapeHtml(msg.content);
        } else {
            // Render markdown for bot messages
            contentHtml = renderMarkdown(msg.content);
        }
        
        // Add typing indicator for streaming messages
        let typingHtml = '';
        if (msg.streaming && !msg.content) {
            typingHtml = `
                <div class="typing-indicator">
                    <span></span><span></span><span></span>
                </div>`;
        }
        
        const timeRefHtml = !isUser 
            ? `<span class="timestamp-ref">📍 Based on video up to ${timeStr}</span>` 
            : '';
        
        html += `
            <div class="message ${roleClass}">
                <div class="message-avatar">${avatarEmoji}</div>
                <div class="message-content">
                    ${timeRefHtml}
                    ${contentHtml}
                    ${typingHtml}
                </div>
            </div>`;
    }
    
    messagesEl.innerHTML = html;
    
    // Syntax highlighting
    messagesEl.querySelectorAll('pre code').forEach(block => {
        if (typeof Prism !== 'undefined') {
            Prism.highlightElement(block);
        }
    });
    
    // Auto-scroll to bottom
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addSystemMessage(text) {
    chatMessages.push({ role: 'system', content: text });
    renderMessages();
}


// ═══════════════════════════════════
//  CHECKPOINT MCQ MODAL (non-skippable)
// ═══════════════════════════════════
function showCheckpointModal(cp) {
    activeCheckpoint = cp;
    mcqShownAt = Date.now();
    mcqAttempts = 0;

    const modal = document.getElementById('checkpointModal');
    const body = document.getElementById('checkpointBody');
    const mins = Math.floor(cp.checkpoint_time / 60);

    let optsHtml = cp.options.map((opt, i) =>
        `<button class="cp-option" data-index="${i}" onclick="selectCheckpointOption(${i})">
            <span class="cp-letter">${String.fromCharCode(65 + i)}</span> ${escapeHtml(opt)}
         </button>`).join('');

    body.innerHTML = `
        <div class="cp-badge">⏸️ Checkpoint · ${mins} min</div>
        <p class="cp-instruction">Answer to continue — you can't skip this.</p>
        <div class="cp-confidence">
            <span>How confident are you?</span>
            <div class="cp-conf-btns">
                ${[1,2,3,4,5].map(n => `<button class="cp-conf" data-c="${n}" onclick="setCheckpointConfidence(${n})">${n}</button>`).join('')}
            </div>
        </div>
        <div class="cp-question">${escapeHtml(cp.question)}</div>
        <div class="cp-options">${optsHtml}</div>
        <div class="cp-feedback" id="cpFeedback"></div>
        <button class="cp-submit" id="cpSubmit" onclick="submitCheckpoint()" disabled>Submit Answer</button>`;

    modal.classList.add('visible');
    window._cpSelected = null;
    window._cpConfidence = 3;
}

function selectCheckpointOption(i) {
    window._cpSelected = i;
    document.querySelectorAll('.cp-option').forEach(b =>
        b.classList.toggle('selected', parseInt(b.dataset.index) === i));
    document.getElementById('cpSubmit').disabled = false;
}

function setCheckpointConfidence(n) {
    window._cpConfidence = n;
    document.querySelectorAll('.cp-conf').forEach(b =>
        b.classList.toggle('selected', parseInt(b.dataset.c) === n));
}

async function submitCheckpoint() {
    if (window._cpSelected === null || !activeCheckpoint) return;
    mcqAttempts += 1;
    const submitBtn = document.getElementById('cpSubmit');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Checking...';

    const tta = mcqShownAt ? (Date.now() - mcqShownAt) / 1000 : null;
    try {
        const resp = await fetch('/api/v1/video/checkpoint/answer', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser ? currentUser.username : 'anonymous',
                video_filename: currentVideoFilename,
                checkpoint_time: activeCheckpoint.checkpoint_time,
                selected_index: window._cpSelected,
                confidence: window._cpConfidence,
                time_to_answer_sec: tta,
                attempts: mcqAttempts
            })
        });
        const data = await resp.json();
        const fb = document.getElementById('cpFeedback');

        if (data.is_correct) {
            fb.innerHTML = `<div class="cp-correct">✅ Correct! ${escapeHtml(data.explanation || '')}</div>`;
            answeredCheckpoints.add(activeCheckpoint.checkpoint_time);
            submitBtn.textContent = 'Continue ▶';
            submitBtn.disabled = false;
            submitBtn.onclick = closeCheckpointAndResume;
        } else {
            // Non-skippable: wrong answer must be retried. Clear selection and
            // keep submit disabled until they pick a new option.
            fb.innerHTML = `<div class="cp-wrong">❌ Not quite — review and try again.</div>`;
            submitBtn.textContent = 'Submit Answer';
            submitBtn.disabled = true;
            window._cpSelected = null;
            document.querySelectorAll('.cp-option').forEach(b => b.classList.remove('selected'));
        }
    } catch (e) {
        console.error('Checkpoint submit failed', e);
        submitBtn.textContent = 'Submit Answer';
        submitBtn.disabled = false;
    }
}

function closeCheckpointAndResume() {
    document.getElementById('checkpointModal').classList.remove('visible');
    activeCheckpoint = null;
    lastTimeUpdate = videoEl.currentTime;
    // If this was the end-of-video checkpoint, go to reflection instead of replaying
    const nearEnd = videoDuration && videoEl.currentTime >= videoDuration * 0.97;
    if (videoEl.ended || nearEnd) {
        endSequenceShown = true;
        showReflectionModal();  // reflection → then the complex-problem popup
    } else {
        videoEl.play().catch(() => {});
    }
}


// ═══════════════════════════════════
//  SRL — PRE-VIDEO INTENTION & POST-VIDEO REFLECTION
// ═══════════════════════════════════
function showIntentionModal() {
    const modal = document.getElementById('intentionModal');
    if (!modal) { intentionGiven = true; return; }
    modal.classList.add('visible');
    setTimeout(() => { const i = document.getElementById('intentionInput'); if (i) i.focus(); }, 100);
}

async function submitIntention() {
    const val = (document.getElementById('intentionInput').value || '').trim();
    if (currentUser && val) {
        fetch('/api/v1/video/reflection', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username, video_filename: currentVideoFilename,
                phase: 'intention', prompt: 'What do you want to learn from this video?', response: val
            })
        }).catch(() => {});
    }
    intentionGiven = true;
    document.getElementById('intentionModal').classList.remove('visible');
    videoEl.play().catch(() => {});
}

function skipIntention() {
    intentionGiven = true;
    document.getElementById('intentionModal').classList.remove('visible');
    videoEl.play().catch(() => {});
}

function showReflectionModal() {
    const modal = document.getElementById('reflectionModal');
    if (!modal) return;
    modal.classList.add('visible');
    setTimeout(() => { const i = document.getElementById('reflectionInput'); if (i) i.focus(); }, 100);
}

async function submitReflection() {
    const val = (document.getElementById('reflectionInput').value || '').trim();
    if (currentUser && val) {
        fetch('/api/v1/video/reflection', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: currentUser.username, video_filename: currentVideoFilename,
                phase: 'reflection', prompt: 'What was your key takeaway?', response: val
            })
        }).catch(() => {});
    }
    document.getElementById('reflectionModal').classList.remove('visible');
    // Lead into the end-of-video complex problem
    showPrelabModal();
}


// ═══════════════════════════════════
//  END-OF-VIDEO COMPLEX PROBLEM (PRELAB)
// ═══════════════════════════════════
function showPrelabModal() {
    if (!prelabProblems || prelabProblems.length === 0) return;
    const modal = document.getElementById('prelabModal');
    const body = document.getElementById('prelabBody');
    if (!modal || !body) return;

    // Pick one problem to feature (rotate/random so students don't all get the same)
    const idx = Math.floor(Math.random() * prelabProblems.length);
    const problem = prelabProblems[idx];
    window._prelabChosen = problem;

    body.innerHTML = `
        <p class="cp-instruction">You've finished the lecture — ready to apply it? Try this challenge.</p>
        <div class="cp-question">${escapeHtml(problem)}</div>
        <div class="prelab-actions">
            <button class="cp-skip" onclick="dismissPrelab()">Maybe later</button>
            <button class="cp-submit-inline" onclick="solveInSage()">🚀 Solve in SAGE</button>
        </div>
        ${prelabProblems.length > 1
            ? `<button class="prelab-shuffle" onclick="showPrelabModal()">↻ Show a different problem</button>`
            : ''}`;
    modal.classList.add('visible');
}

function dismissPrelab() {
    document.getElementById('prelabModal').classList.remove('visible');
}

function solveInSage() {
    const problem = window._prelabChosen;
    if (!problem) return;
    // Telemetry: student chose to transfer video learning into a guided problem
    if (currentUser) {
        fetch('/api/v1/video/prelab-start', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
            body: JSON.stringify({
                username: currentUser.username, video_filename: currentVideoFilename, problem: problem
            })
        }).catch(() => {});
    }
    // Hand off to the main chat in guided complex-problem mode
    const payload = encodeURIComponent('[SOLVE_PRELAB] ' + problem);
    window.location.href = `index.html?initial_msg=${payload}`;
}


// ═══════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderMarkdown(text) {
    if (!text) return '';
    
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            breaks: true,
            gfm: true,
            highlight: function(code, lang) {
                if (typeof Prism !== 'undefined' && lang && Prism.languages[lang]) {
                    return Prism.highlight(code, Prism.languages[lang], lang);
                }
                return code;
            }
        });
        return marked.parse(text);
    }
    
    // Fallback: basic newline handling
    return text.replace(/\n/g, '<br>');
}

function autoResizeInput() {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

// Navigate back to the main chat
function goBack() {
    window.location.href = '/';
}
