const API_URL = "";
const currentUser = JSON.parse(localStorage.getItem('c_tutor_user'));

// Auth Guard
if (!currentUser) window.location.href = 'index.html';

const TOPICS = ["Variables", "Control Flow", "Functions", "Arrays", "Strings", "Pointers", "Structures"];

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

        // FIX: Pass empty dict if null to prevent skill bar crash
        renderSkillBars(data.mastery || {});
    } catch (e) {
        console.error("Stats load failed", e);
    }
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
                label: 'Proficiency Level',
                data: masteryScores,
                backgroundColor: 'rgba(52, 211, 153, 0.15)',
                borderColor: '#34d399',
                pointBackgroundColor: '#34d399',
                borderWidth: 2
            }]
        },
        options: {
            maintainAspectRatio: false,
            scales: {
                r: {
                    min: 0,
                    max: 100,
                    ticks: { display: false },
                    grid: { color: 'rgba(255,255,255,0.06)' },
                    pointLabels: { color: '#94a3b8', font: { size: 11, family: 'Inter' } },
                    angleLines: { color: 'rgba(255,255,255,0.04)' }
                }
            },
            plugins: { legend: { display: false } }
        }
    });
}

// --- SKILL BAR RENDERER ---
function renderSkillBars(mastery) {
    const skillDiv = document.getElementById('skillList');
    skillDiv.innerHTML = '';

    TOPICS.forEach((t) => {
        const score = mastery[t] || 0;
        let gradient = 'linear-gradient(90deg, #f87171, #fb923c)';
        if (score > 30) gradient = 'linear-gradient(90deg, #fbbf24, #facc15)';
        if (score > 70) gradient = 'linear-gradient(90deg, #34d399, #2dd4bf)';

        const safeId = t.replace(/\s+/g, '_');
        skillDiv.innerHTML += `
        <div class="skill-item skill-item-clickable" onclick="toggleMasteryPanel('${safeId}', '${t}')">
            <div style="display:flex; justify-content:space-between; margin-bottom:6px; font-weight:500; font-size:0.85rem;">
                <span>${t}</span>
                <span style="color:#94a3b8;">${score}/100 XP <span class="mastery-expand-hint">▼</span></span>
            </div>
            <div class="progress-bar">
                <div class="fill" style="width:${score}%; background:${gradient};"></div>
            </div>
            <div class="mastery-panel" id="panel-${safeId}" style="display:none;">
                <div class="mastery-panel-loading">Loading mastery data...</div>
            </div>
        </div>`;
    });
}

// --- SRL-BKT CALIBRATION: MASTERY SLIDER PANEL ---
async function toggleMasteryPanel(safeId, concept) {
    const panel = document.getElementById(`panel-${safeId}`);
    if (!panel) return;

    if (panel.style.display === 'block') {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = 'block';
    panel.innerHTML = '<div class="mastery-panel-loading">Loading mastery data...</div>';

    try {
        const res = await fetch(`${API_URL}/api/v1/mastery/${currentUser.username}/${encodeURIComponent(concept)}`);
        const data = await res.json();
        renderMasterySliders(panel, safeId, concept, data);
    } catch (e) {
        panel.innerHTML = '<div class="mastery-panel-loading" style="color:#f87171;">Failed to load mastery data</div>';
        console.error("Mastery panel load failed", e);
    }
}

function renderMasterySliders(panel, safeId, concept, data) {
    const tiers = ['quiz', 'micro', 'code'];
    const tierLabels = { quiz: 'Quiz (Declarative)', micro: 'Micro-Challenge (Procedural)', code: 'Code Review (Applied)' };

    let html = '<div class="mastery-sliders" onclick="event.stopPropagation()">';
    html += '<div class="mastery-panel-title">Self-Assessment — Adjust if BKT overestimates your knowledge</div>';

    tiers.forEach(tier => {
        const t = data.tiers[tier];
        const pBkt = t.p_bkt;
        const pSelf = t.self_assessment !== null ? t.self_assessment : pBkt;
        const pEff = t.p_effective;
        const adapted = t.adapted_P_G !== null;
        const maxVal = pBkt;

        html += `
        <div class="mastery-tier-row">
            <div class="mastery-tier-label">
                <span>${tierLabels[tier]}</span>
                <span class="mastery-tier-values">
                    BKT: <strong>${(pBkt * 100).toFixed(0)}%</strong>
                    ${t.self_assessment !== null ? ` | Self: <strong>${(pSelf * 100).toFixed(0)}%</strong>` : ''}
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
            <div class="mastery-tier-evidence">n_evidence: ${t.n_evidence}</div>
        </div>`;
    });

    html += `
        <div class="mastery-panel-actions">
            <button class="mastery-save-btn" onclick="saveSelfAssessment('${safeId}', '${concept}')">Save Assessment</button>
            <span class="mastery-save-status" id="status-${safeId}"></span>
        </div>
        <div class="mastery-panel-note">Slider can only go <strong>down</strong> — "I know less than shown." To raise mastery, ask to be quizzed.</div>
    </div>`;

    panel.innerHTML = html;
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

// Start!
initDashboard();