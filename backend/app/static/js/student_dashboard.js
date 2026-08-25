const API_URL = "";
const currentUser = JSON.parse(localStorage.getItem('c_tutor_user'));

// Auth Guard
if (!currentUser) window.location.href = 'index.html';

const TOPICS = ["Variables", "Control Flow", "Functions", "Arrays", "Strings",
               "Pointers", "Structures", "Memory Allocation", "File I/O"];

// --- MAIN INIT ---
function initDashboard() {
    // 1. Load Fast Data (Charts & Stats)
    loadStats();
    // 2. Load Slow Data (LLM Report) - Running in parallel
    loadReport();
    loadAssignments();
    fetchLearningPath();
}

// --- FAST DATA LOADER ---
async function loadStats() {
    try {
        const resStats = await fetch(`${API_URL}/api/v1/analytics/student/${currentUser.username}`);
        const data = await resStats.json();

        // Goal
        document.getElementById('goalDisplay').innerText = data.goal || "Set a goal (e.g. Master Pointers)";

        // Visuals
        renderCharts(data);

        // Render the prerequisite skill network (nodes = topics, edges = prereqs)
        renderSkillNetwork(data.mastery || {});

        // Metacognitive calibration panel (only renders if the MCN feature is on)
        loadCalibration();
    } catch (e) {
        console.error("Stats load failed", e);
    }
}

// --- METACOGNITIVE CALIBRATION (MCN) PANEL ---
async function loadCalibration() {
    const card = document.getElementById('calibrationCard');
    const list = document.getElementById('calibrationList');
    if (!card || !list) return;
    try {
        const res = await fetch(`${API_URL}/api/v1/mcn/calibration/${currentUser.username}`);
        const data = await res.json();
        // Feature off, or no topics with enough signal → keep the card hidden.
        if (!data.enabled || !Array.isArray(data.items) || data.items.length === 0) {
            card.style.display = 'none';
            return;
        }
        const META = {
            under: { cls: 'cal-under', icon: '💪', tag: 'You know this better than you think' },
            over:  { cls: 'cal-over',  icon: '🔎', tag: 'Worth a quick double-check' },
            cal:   { cls: 'cal-ok',    icon: '✅', tag: 'Confidence matches performance' },
        };
        list.innerHTML = '';
        data.items.forEach(it => {
            const m = META[it.state] || META.cal;
            const pct = Math.round((it.confidence || 0) * 100);
            const el = document.createElement('div');
            el.className = `calibration-item ${m.cls}`;
            el.innerHTML =
                `<div class="cal-head">
                    <span class="cal-icon">${m.icon}</span>
                    <span class="cal-concept">${escapeHTMLc(it.concept)}</span>
                    <span class="cal-badge">${escapeHTMLc(it.label || '')}</span>
                 </div>
                 <div class="cal-tag">${m.tag}</div>
                 <div class="cal-conf" title="model confidence">confidence ${pct}%</div>`;
            list.appendChild(el);
        });
        card.style.display = 'block';
    } catch (e) {
        console.error("Calibration load failed", e);
        card.style.display = 'none';
    }
}

function escapeHTMLc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// --- SLOW DATA LOADER ---
async function loadReport() {
    try {
        const resReport = await fetch(`${API_URL}/api/v1/analytics/report/${currentUser.username}`);
        const rep = await resReport.json();

        // Show Content
        document.getElementById('loadingReport').style.display = 'none';
        document.getElementById('reportContent').style.display = 'grid';

        // Populate Text
        document.getElementById('rep-focus').innerText = rep.focus || "No data";
        document.getElementById('rep-strengths').innerText = rep.strengths || "N/A";
        document.getElementById('rep-weakness').innerText = rep.weakness || "N/A";

        // Populate Chips
        const readDiv = document.getElementById('rep-reading');
        readDiv.innerHTML = '';
        if (rep.reading && Array.isArray(rep.reading)) {
            rep.reading.forEach(item => {
                readDiv.innerHTML += `<span class="reading-chip">📖 ${item}</span>`;
            });
        } else {
            readDiv.innerText = "Keep practicing basic concepts.";
        }
    } catch (e) {
        console.error("Report load failed", e);
        document.getElementById('loadingReport').innerText = "Analysis unavailable.";
    }
}

// --- CHART RENDERER ---
function renderCharts(data) {
    // 1. Safety Check: Default to empty object if null/undefined
    const stats = data.stats || { CONCEPT: 0, PROBLEM: 0, DEBUG: 0, REVIEW: 0 };
    const mastery = data.mastery || {};

    // Destroy existing charts to prevent canvas reuse errors (glitching)
    if (window.topicChartRef) window.topicChartRef.destroy();
    if (window.radarChartRef) window.radarChartRef.destroy();

    // 2. Donut Chart (Habits)
    const ctxDonut = document.getElementById('donutChart').getContext('2d');
    window.topicChartRef = new Chart(ctxDonut, {
        type: 'doughnut',
        data: {
            labels: ['Concept (Reading)', 'Problem (Planning)', 'Debug (Fixing)', 'Review (Coding)'],
            datasets: [{
                data: [
                    stats.CONCEPT || 0,
                    stats.PROBLEM || 0,
                    stats.DEBUG || 0,
                    stats.REVIEW || 0
                ],
                backgroundColor: ['#60a5fa', '#a78bfa', '#f87171', '#fbbf24'],
                borderWidth: 2,
                borderColor: '#0f1115'
            }]
        },
        options: {
            maintainAspectRatio: false,
            plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11, family: 'Inter' }, color: '#e2e8f0' } } }
        }
    });

    // 3. Radar Chart (Proficiency)
    // CRITICAL FIX: Use the safe 'mastery' object we defined at the top
    const masteryScores = TOPICS.map(t => mastery[t] || 0);

    const ctxRadar = document.getElementById('radarChart').getContext('2d');
    window.radarChartRef = new Chart(ctxRadar, {
        type: 'radar',
        data: {
            labels: TOPICS,
            datasets: [{
                label: 'Mastery',
                data: masteryScores,
                // Stronger fill and a closed outline: this reads as a SHAPE showing
                // coverage across the curriculum. It previously looked like a stray
                // dot near the origin because it was plotting the intent-XP heuristic
                // (values of 0-20) rather than real mastery.
                backgroundColor: 'rgba(96, 165, 250, 0.22)',
                borderColor: '#60a5fa',
                borderWidth: 2,
                fill: true,
                tension: 0,
                pointBackgroundColor: '#60a5fa',
                pointBorderColor: '#0f1115',
                pointBorderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 5
            }]
        },
        options: {
            maintainAspectRatio: false,
            scales: {
                r: {
                    min: 0,
                    max: 100,
                    beginAtZero: true,
                    // Show the scale — an unlabelled radar gives no sense of whether
                    // the shape is large or small.
                    ticks: {
                        display: true,
                        stepSize: 25,
                        showLabelBackdrop: false,
                        color: '#64748b',
                        font: { size: 9, family: 'Inter' },
                        callback: v => (v === 0 ? '' : v + '%')
                    },
                    grid: { color: 'rgba(255,255,255,0.06)' },
                    pointLabels: { color: '#94a3b8', font: { size: 11, family: 'Inter' } },
                    angleLines: { color: 'rgba(255,255,255,0.04)' }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: c => `${c.label}: ${c.raw}% mastery`
                    }
                }
            }
        }
    });
}

// --- SKILL NETWORK RENDERER ---
// Fixed layered layout for the C-curriculum topics (prereqs flow left → right).
const NET_POS = {
    "Variables":         { x: 60,  y: 200 },
    "Control Flow":      { x: 190, y: 115 },
    "Pointers":          { x: 190, y: 290 },
    "Functions":         { x: 340, y: 65  },
    "Arrays":            { x: 340, y: 175 },
    "Memory Allocation": { x: 340, y: 300 },
    "Strings":           { x: 500, y: 110 },
    "Structures":        { x: 500, y: 215 },
    "File I/O":          { x: 645, y: 110 },
};
const NET_R = 30;                       // node radius
const NET_CIRC = 2 * Math.PI * NET_R;   // ring circumference

// Colour encodes STATE; the ring's arc length already encodes magnitude, so grading
// the colour by score too was doubling up on one variable — and doing it badly: any
// topic under 30 came out red, so an untouched topic read as "failing" and a new
// student's graph was a wall of alarm. Red also meant "Debug" in the habits legend
// one card over. Reserve it for nothing here; no topic state warrants it.
function scoreColor(score) {
    if (score <= 0) return '#475569';   // no evidence yet — neutral slate
    return '#60a5fa';                   // in progress (the legend's "mastery ring")
}

async function renderSkillNetwork(mastery) {
    window._skillMastery = mastery;
    let net;
    try {
        const res = await fetch(`${API_URL}/api/v1/skill-network/${currentUser.username}`);
        net = await res.json();
    } catch (e) {
        console.error("Skill network load failed", e);
        net = { nodes: TOPICS.map(c => ({ concept: c })), edges: [] };
    }
    window._skillNet = net;
    drawNetwork(mastery, net);
    drawLegend();
}

function drawNetwork(mastery, net) {
    const host = document.getElementById('skillNetwork');
    if (!host) return;
    const byConcept = {};
    (net.nodes || []).forEach(n => { byConcept[n.concept] = n; });

    // --- edges (prereq → dependent) ---
    let edgesSvg = '';
    (net.edges || []).forEach(([from, to]) => {
        const a = NET_POS[from], b = NET_POS[to];
        if (!a || !b) return;
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        const x1 = a.x + ux * NET_R, y1 = a.y + uy * NET_R;
        const x2 = b.x - ux * (NET_R + 8), y2 = b.y - uy * (NET_R + 8);
        // amber "flow" when a certified prereq is head-starting a zero-evidence dependent
        const flowing = byConcept[from]?.ever_certified && byConcept[to]?.has_head_start;
        edgesSvg += `<line class="sedge ${flowing ? 'flow' : ''}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"
                       marker-end="url(#${flowing ? 'net-arrow-flow' : 'net-arrow'})"/>`;
    });

    // --- nodes ---
    let nodesSvg = '';
    TOPICS.forEach(concept => {
        const p = NET_POS[concept];
        if (!p) return;
        const info = byConcept[concept] || {};
        const score = Math.round(mastery[concept] || 0);
        const certified = info.ever_certified;
        const headStart = info.has_head_start;
        let ring = scoreColor(score);
        if (certified) ring = '#34d399';
        else if (headStart) ring = '#fbbf24';
        // A certified topic draws a FULL ring. The arc length otherwise comes from
        // the XP score, which is an activity heuristic — so a certified node was
        // rendering a 5%-long green stub that read as "barely started".
        const dash = (certified ? 1 : score / 100) * NET_CIRC;
        const badge = certified
            ? `<text class="snode-badge" y="-${NET_R + 8}" fill="#34d399">✓ certified</text>`
            : (headStart ? `<text class="snode-badge" y="-${NET_R + 8}" fill="#fbbf24">+ head start</text>` : '');
        nodesSvg += `
        <g class="snode ${certified ? 'certified' : ''}" data-concept="${concept}" transform="translate(${p.x},${p.y})"
           role="button" tabindex="0" aria-label="${concept}, ${score} percent mastery">
            ${badge}
            <circle class="snode-bg" r="${NET_R}"></circle>
            <circle class="snode-ring" r="${NET_R}" transform="rotate(-90)"
                    stroke="${ring}" stroke-dasharray="${dash} ${NET_CIRC}"></circle>
            <circle class="snode-core" r="${NET_R - 6}"></circle>
            <text class="snode-pct" y="5">${score}%</text>
            <text class="snode-lbl" y="${NET_R + 20}">${concept}</text>
        </g>`;
    });

    host.innerHTML = `
    <svg viewBox="0 0 720 380" role="img" aria-label="Skill prerequisite network">
        <defs>
            <marker id="net-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="rgba(255,255,255,0.22)"></path>
            </marker>
            <marker id="net-arrow-flow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="#fbbf24"></path>
            </marker>
        </defs>
        <g class="sedges">${edgesSvg}</g>
        <g class="snodes">${nodesSvg}</g>
    </svg>`;

    // wire clicks / keyboard
    host.querySelectorAll('.snode').forEach(g => {
        const concept = g.getAttribute('data-concept');
        g.addEventListener('click', () => openNodePanel(concept));
        g.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openNodePanel(concept); }
        });
    });
}

function drawLegend() {
    const el = document.getElementById('skillLegend');
    if (!el) return;
    el.innerHTML = `
        <span class="leg"><i class="dot" style="background:#34d399"></i>certified</span>
        <span class="leg"><i class="dot" style="background:#60a5fa"></i>in progress</span>
        <span class="leg"><i class="dot" style="background:#fbbf24"></i>head start</span>
        <span class="leg"><i class="dot" style="background:#475569"></i>not started</span>
        <span class="leg"><i class="arrowhint"></i>requires →</span>`;
}

// --- SRL-BKT CALIBRATION: MASTERY SLIDER PANEL (opened from a node) ---
let _activeNode = null;
async function openNodePanel(concept) {
    const panel = document.getElementById('nodePanel');
    if (!panel) return;
    const safeId = concept.replace(/\s+/g, '_');

    // clicking the already-open node closes the panel
    if (_activeNode === concept) {
        panel.innerHTML = '';
        _activeNode = null;
        highlightNode(null);
        return;
    }
    _activeNode = concept;
    highlightNode(concept);

    panel.innerHTML = '<div class="mastery-panel-loading">Loading mastery data…</div>';
    try {
        const res = await fetch(`${API_URL}/api/v1/mastery/${currentUser.username}/${encodeURIComponent(concept)}`);
        const data = await res.json();
        renderMasterySliders(panel, safeId, concept, data);
    } catch (e) {
        panel.innerHTML = '<div class="mastery-panel-loading" style="color:#f87171;">Failed to load mastery data</div>';
        console.error("Mastery panel load failed", e);
    }
}

function highlightNode(concept) {
    document.querySelectorAll('.snode').forEach(g => {
        g.classList.toggle('selected', g.getAttribute('data-concept') === concept);
    });
}

function renderMasterySliders(panel, safeId, concept, data) {
    const tiers = ['quiz', 'micro', 'code'];
    const tierLabels = { quiz: 'Quiz (Declarative)', micro: 'Micro-Challenge (Procedural)', code: 'Code Review (Applied)' };

    let html = '<div class="mastery-sliders" onclick="event.stopPropagation()">';
    html += `
        <div class="mastery-panel-header">
            <span class="mastery-panel-heading">${concept} — self-assessment</span>
            <div class="info-icon">ⓘ
                <span class="tooltip-text">
                    Drag a tier <strong>down</strong> if the tutor overestimates what you know
                    ("I know less than shown"). The slider only goes down — to raise mastery, ask to be quizzed.
                    Your input changes how the model weighs future evidence, but never certifies you.
                </span>
            </div>
            <button class="mastery-close-btn" onclick="openNodePanel('${concept.replace(/'/g, "\\'")}')" aria-label="Close">✕</button>
        </div>`;

    tiers.forEach(tier => {
        const t = data.tiers[tier];
        const pBkt = t.p_bkt;
        const pSelf = t.p_self != null ? t.p_self : pBkt;
        const pEff = t.p_effective;
        const adapted = t.adapted_P_G !== null;
        const maxVal = pBkt;

        // How many correct-in-a-row still needed to certify this tier.
        const need = (typeof t.answers_to_master === 'number') ? t.answers_to_master : null;
        let targetHtml;
        if (need === 0) {
            targetHtml = `<span class="mastery-tier-target mastered">✓ tier certified</span>`;
        } else if (need !== null) {
            const word = need === 1 ? 'answer' : 'answers';
            targetHtml = `<span class="mastery-tier-target">🎯 ≈ ${need} correct ${word} in a row to certify</span>`;
        } else {
            targetHtml = '';
        }

        html += `
        <div class="mastery-tier-row">
            <div class="mastery-tier-label">
                <span>${tierLabels[tier]}</span>
                <span class="mastery-tier-values">
                    BKT: <strong>${(pBkt * 100).toFixed(0)}%</strong>
                    ${t.p_self != null ? ` | Self: <strong>${(pSelf * 100).toFixed(0)}%</strong>` : ''}
                    | Eff: <strong id="eff-${safeId}-${tier}">${(pEff * 100).toFixed(0)}%</strong>
                    ${adapted ? ' <span class="mastery-adapted-badge">P_G adapted</span>' : ''}
                </span>
            </div>
            <div class="mastery-slider-row">
                <input type="range" class="mastery-slider" id="slider-${safeId}-${tier}"
                    min="0" max="${(maxVal * 100).toFixed(0)}" step="1"
                    value="${(pSelf * 100).toFixed(0)}"
                    oninput="onSliderMove('${safeId}', '${tier}', this.value, ${pBkt})">
                <span class="mastery-slider-value" id="val-${safeId}-${tier}">${(pSelf * 100).toFixed(0)}%</span>
            </div>
            <div class="mastery-tier-foot">
                <span class="mastery-tier-evidence">evidence ${t.n_evidence}/3</span>
                ${targetHtml}
            </div>
        </div>`;
    });

    const cEsc = concept.replace(/'/g, "\\'");
    html += `
        <div class="mastery-panel-actions">
            <button class="mastery-save-btn" onclick="saveSelfAssessment('${safeId}', '${concept}')">Save Assessment</button>
            <button class="mastery-verify-btn" onclick="verifyMastery('${cEsc}')" title="Answer quiz questions to raise your Quiz tier. Full certification also needs the Micro (Your Turn code challenges) and Code (submit code for review) tiers.">🎯 Verify Mastery</button>
            <span class="mastery-save-status" id="status-${safeId}"></span>
        </div>
        ${masteryHint(concept, data)}
    </div>`;

    panel.innerHTML = html;
}

// "What else to do" — weakest tier + any uncertified prerequisite.
function masteryHint(concept, data) {
    const labels = { quiz: 'Quiz', micro: 'Micro-Challenge', code: 'Code Review' };
    // How a student earns each tier — so they know which action feeds which bar.
    const howto = {
        quiz: 'answer quiz questions or click 🎯 Verify Mastery',
        micro: 'answer the tutor\'s "Your Turn" challenges with a line of C code',
        code: 'submit code and ask for a review',
    };
    const parts = [];

    // 1. Weakest tier = the one furthest from certification (most answers still needed).
    let worst = null, worstN = -1;
    ['quiz', 'micro', 'code'].forEach(t => {
        const n = data.tiers[t] && data.tiers[t].answers_to_master;
        if (typeof n === 'number' && n > worstN) { worstN = n; worst = t; }
    });
    if (worstN > 0) {
        parts.push(`Focus your <b>${labels[worst]}</b> tier — about ${worstN} correct in a row to go. To earn it: ${howto[worst]}.`);
    }

    // 2. Prerequisites that are not yet certified (from the skill-network data).
    const net = window._skillNet;
    if (net && net.edges) {
        const byC = {};
        (net.nodes || []).forEach(n => { byC[n.concept] = n; });
        const missing = net.edges
            .filter(([pre, dep]) => dep === concept && byC[pre] && !byC[pre].ever_certified)
            .map(([pre]) => pre);
        if (missing.length) {
            parts.push(`Prerequisite not yet certified: <b>${missing.join(', ')}</b> — mastering it gives this topic a head start.`);
        }
    }

    if (!parts.length) return `<div class="mastery-hint success">🎉 This topic is fully certified — nice work!</div>`;
    return `<div class="mastery-hint">💡 ${parts.join(' ')}</div>`;
}

// Deep-link to the chat and fire the verify quiz (the earned path UP).
function verifyMastery(concept) {
    const msg = encodeURIComponent(`[VERIFY_MASTERY] ${concept}`);
    window.location.href = `index.html?initial_msg=${msg}`;
}

function onSliderMove(safeId, tier, value, pBkt) {
    const pSelf = value / 100;
    const pEff = 0.6 * pBkt + 0.4 * pSelf;
    document.getElementById(`val-${safeId}-${tier}`).textContent = `${value}%`;
    document.getElementById(`eff-${safeId}-${tier}`).textContent = `${(pEff * 100).toFixed(0)}%`;
}

async function saveSelfAssessment(safeId, concept) {
    const tiers = ['quiz', 'micro', 'code'];
    const statusEl = document.getElementById(`status-${safeId}`);
    statusEl.textContent = 'Saving...';
    statusEl.style.color = 'var(--accent-color)';

    let anyError = false;
    for (const tier of tiers) {
        const slider = document.getElementById(`slider-${safeId}-${tier}`);
        if (!slider) continue;
        const pSelf = parseInt(slider.value) / 100;
        const pBktMax = parseFloat(slider.max) / 100;

        if (pSelf >= pBktMax) continue;

        try {
            const res = await fetch(`${API_URL}/api/v1/mastery/self-assess`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: currentUser.username,
                    concept: concept,
                    tier: tier,
                    self_assessment: pSelf
                })
            });
            if (!res.ok) {
                const err = await res.json();
                console.warn(`Self-assess ${tier} failed:`, err.detail);
            }
        } catch (e) {
            console.error(`Self-assess ${tier} error:`, e);
            anyError = true;
        }
    }

    if (anyError) {
        statusEl.textContent = 'Some tiers failed to save';
        statusEl.style.color = '#f87171';
    } else {
        statusEl.textContent = 'Saved!';
        statusEl.style.color = 'var(--success-color)';
        setTimeout(() => { statusEl.textContent = ''; }, 2000);
        // Doubt flows downhill: a saved downgrade may claw back head starts on
        // dependent nodes, so refresh the network to reflect the new state.
        if (window._skillMastery) { renderSkillNetwork(window._skillMastery); }
    }
}

// --- GOAL LOGIC ---
function toggleEdit() {
    const display = document.getElementById('goalDisplay');
    const edit = document.getElementById('goalEdit');
    if (edit.style.display === 'none') {
        edit.style.display = 'block';
        display.style.display = 'none';
        // Don't pre-fill if it's the default "Loading..." or "Set a goal..." text
        const currentText = display.innerText;
        if (!currentText.includes("Loading") && !currentText.includes("Set a goal")) {
            document.getElementById('goalInput').value = currentText;
        } else {
            document.getElementById('goalInput').value = ""; // Let placeholder show
        }
    } else {
        edit.style.display = 'none';
        display.style.display = 'block';
    }
}

async function saveGoal() {
    const newGoal = document.getElementById('goalInput').value;
    if (!newGoal.trim()) return;

    // 1. Update Button UI to show loading state
    const saveBtn = document.querySelector('#goalEdit .goal-btn');
    const originalBtnText = saveBtn.innerText;
    saveBtn.innerText = "Generating Path... ⏳";
    saveBtn.disabled = true;

    try {
        // 2. Save to backend
        await fetch(`${API_URL}/api/v1/user/goal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUser.username, goal: newGoal })
        });

        // 3. Update the Goal Title UI and hide the edit box
        document.getElementById('goalDisplay').innerText = newGoal;
        toggleEdit();

        // 4. Show a loading state in the Learning Path section
        const container = document.getElementById('learningPathCard');
        const tree = document.getElementById('learningPathTree');
        container.style.display = 'block';
        tree.innerHTML = '<div style="color: var(--accent-color); padding: 15px; font-weight: 500; font-size: 0.95rem;">🤖 AI is generating your personalized learning path...</div>';

        // 5. Fetch and render the new path
        await fetchLearningPath();

    } catch (e) {
        console.error("Failed to save goal", e);
        alert("Failed to generate path. Please try again.");
    } finally {
        // 6. Restore button state
        saveBtn.innerText = originalBtnText;
        saveBtn.disabled = false;
    }
}

// --- LEARNING PATH LOGIC ---
async function fetchLearningPath() {
    try {
        const res = await fetch(`${API_URL}/api/v1/analytics/learning_path/${currentUser.username}`);
        const data = await res.json();

        const container = document.getElementById('learningPathCard');
        const tree = document.getElementById('learningPathTree');

        if (!data.path || data.path.length === 0) {
            container.style.display = 'none';
            return;
        }

        container.style.display = 'block';
        tree.innerHTML = '';

        data.path.forEach((node, index) => {
            let icon = '';
            if (node.status === 'mastered') icon = '✓';
            else if (node.status === 'locked') icon = '🔒';
            else icon = '⭐'; // Next

            let clickAttr = '';
            if (node.status === 'next') {
                // Deep link to chat
                clickAttr = `onclick="startLearning('${node.concept.replace(/'/g, "\\'")}', '${data.goal.replace(/'/g, "\\'")}')"`;
            }

            tree.innerHTML += `
                <div class="path-node-wrapper">
                    <div class="path-node ${node.status}" ${clickAttr} title="${node.status}">
                        ${icon}
                        <span class="path-node-label">${node.concept}</span>
                    </div>
                    ${index < data.path.length - 1 ? `<div class="path-line ${node.status}"></div>` : ''}
                </div>
            `;
        });
    } catch (e) {
        console.error("Learning path load failed", e);
    }
}

function startLearning(concept, goal) {
    // Use a specific hidden tag so the backend tutor knows this is a fresh start
    // and doesn't mistake it for a complex architectural problem.
    const msg = encodeURIComponent(`[START_TOPIC] ${concept} [GOAL] ${goal}`);
    window.location.href = `index.html?initial_msg=${msg}`;
}

// --- ASSIGNMENT LOGIC ---
async function loadAssignments() {
    try {
        const res = await fetch(`${API_URL}/api/v1/assignments/student/${currentUser.username}`);
        const assignments = await res.json();

        const container = document.getElementById('challengesSection');
        const list = document.getElementById('challengeList');

        if (assignments.length === 0) {
            container.style.display = 'none'; // Hide if empty
            return;
        }

        container.style.display = 'block'; // Show if data exists
        list.innerHTML = '';

        // Sort: PENDING first, then others
        assignments.sort((a, b) => (a.status === 'PENDING' ? -1 : 1));

        assignments.forEach(item => {
            let statusBadge = '';
            let contentHtml = '';

            // 0. MATERIAL STATE — reading / code / uploaded file assigned by the teacher.
            if (item.kind === 'material') {
                const done = item.status === 'DONE';
                const kindIcon = item.material_kind === 'code' ? '💻'
                    : (item.material_kind === 'file' ? '📎' : '📄');
                const label = (item.material || 'resource')
                    .replace(/^\d+_/, '').replace(/^demo_/, '').replace(/\.[^.]+$/, '').replace(/_/g, ' ');
                const dl = `${API_URL}/api/v1/materials/download?kind=${encodeURIComponent(item.material_kind || 'reading')}&name=${encodeURIComponent(item.material || '')}`;
                statusBadge = done
                    ? '<span class="challenge-status st-graded">Done</span>'
                    : '<span class="challenge-status st-pending">New</span>';
                list.innerHTML += `
                <div class="challenge-item" style="border-left:3px solid #34d399;">
                    <div class="challenge-header">
                        <span style="color:#777; font-size:0.8rem;">📅 ${new Date(item.timestamp).toLocaleDateString()}</span>
                        ${statusBadge}
                    </div>
                    <p style="font-size:1.02rem; font-weight:600; margin:6px 0;">📚 Your instructor assigned you${item.topic ? ` — <span style="color:#34d399;">${item.topic}</span>` : ''}</p>
                    ${item.question ? `<p style="color:#94a3b8; font-size:0.9rem; margin-bottom:10px;">${item.question}</p>` : ''}
                    <a href="${dl}" target="_blank" rel="noopener" style="display:inline-block; background:#a78bfa; color:#0f1115; padding:8px 16px; border-radius:8px; font-weight:600; text-decoration:none;">${kindIcon} Open ${label}</a>
                    ${done ? '' : `<button onclick="markMaterialDone('${item.id}')" style="margin-left:10px; background:transparent; color:#94a3b8; border:1px solid rgba(255,255,255,0.15); padding:8px 14px; border-radius:8px; cursor:pointer; font-family:Inter,sans-serif;">Mark as done</button>`}
                </div>`;
                return;
            }

            // 1. PENDING STATE (Input Box)
            if (item.status === 'PENDING') {
                statusBadge = '<span class="challenge-status st-pending">Wait for Reply</span>'; // Actually 'Action Required'
                contentHtml = `
                <p style="font-size:1.05rem; font-weight:500; margin-bottom:15px;">${item.question}</p>
                <textarea id="ans-${item.id}" rows="4" style="width:100%; padding:12px; border:1px solid rgba(255,255,255,0.08); border-radius:8px; font-family:inherit; background:rgba(255,255,255,0.03); color:#e2e8f0;" placeholder="Type your solution here..."></textarea>
                <button onclick="submitChallenge('${item.id}')" style="margin-top:10px; background:#a78bfa; color:#0f1115; border:none; padding:8px 20px; border-radius:8px; cursor:pointer; font-weight:600; font-family:Inter,sans-serif;">Submit Answer</button>
            `;
            }
            // 2. SUBMITTED STATE (Read Only)
            else if (item.status === 'SUBMITTED') {
                statusBadge = '<span class="challenge-status st-submitted">Under Review</span>';
                contentHtml = `
                <p style="font-weight:500; color:#94a3b8;">Q: ${item.question}</p>
                <div style="background:rgba(255,255,255,0.03); padding:12px; border-radius:8px; color:#e2e8f0; border:1px solid rgba(255,255,255,0.08);">
                    <b>Your Answer:</b><br>${item.student_answer}
                </div>
                <div style="margin-top:10px; font-size:0.9rem; color:#94a3b8;">Wait for teacher feedback...</div>
            `;
            }
            // 3. GRADED STATE (Show Feedback)
            else if (item.status === 'GRADED') {
                statusBadge = '<span class="challenge-status st-graded">Completed</span>';
                contentHtml = `
                <p style="font-weight:500; color:#94a3b8;">Q: ${item.question}</p>
                <div style="background:rgba(255,255,255,0.03); padding:12px; border-radius:8px; color:#e2e8f0; margin-bottom:10px; border:1px solid rgba(255,255,255,0.08);">
                    <b>Your Answer:</b><br>${item.student_answer}
                </div>
                <div class="teacher-feedback">
                    <b>👨‍🏫 Teacher Feedback:</b><br>
                    ${item.teacher_feedback}
                </div>
            `;
            }

            list.innerHTML += `
            <div class="challenge-item">
                <div class="challenge-header">
                    <span style="color:#777; font-size:0.8rem;">📅 ${new Date(item.timestamp).toLocaleDateString()}</span>
                    ${statusBadge}
                </div>
                ${contentHtml}
            </div>
        `;
        });

    } catch (e) { console.error("Assignment load failed", e); }
}

async function submitChallenge(id) {
    const answer = document.getElementById(`ans-${id}`).value;
    if (!answer.trim()) return alert("Please write an answer first.");

    try {
        const res = await fetch(`${API_URL}/api/v1/assignments/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assignment_id: id, answer: answer })
        });

        if (res.ok) {
            // Reload to show "Submitted" state
            loadAssignments();
        } else {
            alert("Submission failed.");
        }
    } catch (e) { console.error(e); }
}

async function markMaterialDone(id) {
    try {
        const res = await fetch(`${API_URL}/api/v1/assignments/material_done`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assignment_id: id })
        });
        if (res.ok) loadAssignments();
    } catch (e) { console.error("Mark done failed", e); }
}

// Start!
initDashboard();