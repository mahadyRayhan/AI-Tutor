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

// DOM references (assigned in init)
let videoEl, videoSelect, messagesEl, inputEl, sendBtn;
let timeBadge, pausedHint, transcriptStatus, welcomeView;

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
        const resp = await fetch('/api/v1/video/list');
        const data = await resp.json();
        
        videoSelect.innerHTML = '<option value="">— Select a lecture —</option>';
        
        for (const v of data.videos) {
            const opt = document.createElement('option');
            opt.value = v.filename;
            
            // Clean up filename for display
            const displayName = v.filename
                .replace(/\.[^.]+$/, '')       // Remove extension
                .replace(/_/g, ' ')            // Replace underscores
                .replace(/([a-z])([A-Z])/g, '$1 $2');  // CamelCase → spaces
            
            opt.textContent = `${displayName} (${v.size_mb} MB)`;
            if (v.has_transcript) opt.textContent += ' ✓';
            videoSelect.appendChild(opt);
        }

        // Auto-select if only one video
        if (data.videos.length === 1) {
            videoSelect.value = data.videos[0].filename;
            onVideoSelect(data.videos[0].filename);
        }
    } catch (err) {
        console.error('Failed to load video list:', err);
    }
}

function onVideoSelect(filename) {
    if (!filename) return;
    
    currentVideoFilename = filename;
    videoEl.src = `/videos/${encodeURIComponent(filename)}`;
    videoEl.load();
    
    // Reset chat state
    currentTimestamp = 0;
    isPaused = false;
    chatMessages = [];
    
    // Show welcome view
    if (welcomeView) welcomeView.style.display = 'flex';
    renderMessages();
    updateTimeBadge(0);
    updateTranscriptStatus('ready');
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
    
    // Focus the input for quick typing
    setTimeout(() => inputEl.focus(), 100);
}

function onVideoPlay() {
    isPaused = false;
    if (pausedHint) pausedHint.classList.remove('visible');
}

function onTimeUpdate() {
    if (!isPaused) {
        currentTimestamp = videoEl.currentTime;
    }
}

function onVideoLoaded() {
    updateTranscriptStatus('ready');
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
