# backend/app/db/sqlite_db.py

import sqlite3
import json
import os
from app.core import config
from datetime import datetime

# Define path relative to backend root
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# DB_PATH = os.path.join(BASE_DIR, "database", "ai_tutor.db")
DB_PATH = os.path.join(config.DB_DIR, "ai_tutor.db")

class SQLiteDB:
    def __init__(self):
        # Ensure database directory exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        # Connect
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row # Access columns by name
        self._init_tables()

    def _init_tables(self):
        with self.conn:
            # 1. Users Table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT,
                    role TEXT,
                    name TEXT,
                    email TEXT,
                    university TEXT,
                    is_blocked BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP,
                    learning_profile JSON -- <--- NEW: Stores { "frustration_threshold": "low", "preferred_style": "visual" }
                )
            """)
            
            # 1b. Password reset tokens
            # Only a HASH of the token is stored: a leaked database must not yield
            # working reset links, exactly as with passwords themselves.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_token (
                    token_hash TEXT PRIMARY KEY,
                    username   TEXT NOT NULL,
                    created_at TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    used_at    TIMESTAMP,
                    FOREIGN KEY(username) REFERENCES users(username)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reset_user ON password_reset_token(username)")

            # 2. Sessions Table (Chat Rooms)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    username TEXT,
                    title TEXT,
                    state TEXT, -- JSON string for active_plan/quiz
                    created_at TIMESTAMP,
                    deleted BOOLEAN DEFAULT 0,
                    FOREIGN KEY(username) REFERENCES users(username)
                )
            """)

            # 3. Messages Table (Chat Logs)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    username TEXT, -- Redundant but useful for analytics queries
                    role TEXT, -- 'user' or 'bot'
                    content TEXT,
                    sources TEXT, -- JSON string
                    intent TEXT,
                    topic TEXT,
                    timestamp TIMESTAMP,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            """)

            # 4. Assignments Table (Challenges)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY,
                    teacher_id TEXT,
                    student_id TEXT,
                    question TEXT,
                    student_answer TEXT,
                    teacher_feedback TEXT,
                    status TEXT, -- PENDING, SUBMITTED, GRADED
                    timestamp TIMESTAMP
                )
            """)
            
            # 5. User Knowledge (XP/Mastery)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_knowledge (
                    username TEXT,
                    concept TEXT,
                    timestamp TIMESTAMP,
                    PRIMARY KEY (username, concept)
                )
            """)
            
            # 6. User Goals
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_goals (
                    username TEXT PRIMARY KEY,
                    goal_text TEXT,
                    updated_at TIMESTAMP
                )
            """)

            # 7. User Feedback
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    session_id TEXT,
                    original_query TEXT,
                    feedback_type TEXT,
                    feedback_text TEXT, -- NEW COLUMN
                    timestamp TIMESTAMP,
                    FOREIGN KEY(username) REFERENCES users(username),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            """)
            # 8. SRL-BKT Calibration Loop
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_bkt_calibration (
                    username         TEXT    NOT NULL,
                    concept          TEXT    NOT NULL,
                    tier             TEXT    NOT NULL,
                    self_assessment  REAL    DEFAULT NULL,
                    last_adjusted_at TIMESTAMP DEFAULT NULL,
                    n_adjustments    INTEGER DEFAULT 0,
                    direction_ema    REAL    DEFAULT 0.0,
                    adapted_P_G      REAL    DEFAULT NULL,
                    last_adapted_at  TIMESTAMP DEFAULT NULL,
                    PRIMARY KEY (username, concept, tier)
                )
            """)

            # =========================================================
            # CLASS TRIAL TELEMETRY (Data Collection Plan)
            # Append-only logging tables for the one-shot class study.
            # =========================================================

            # Foundation: anonymized Study ID linkage
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS study_participants (
                    study_id    TEXT PRIMARY KEY,
                    username    TEXT UNIQUE,
                    cohort      TEXT,
                    enrolled_at TIMESTAMP
                )
            """)

            # Foundation: append-only raw event stream (the safety net)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id   TEXT,
                    username   TEXT,
                    session_id TEXT,
                    event_type TEXT,
                    payload    TEXT,
                    ts_utc     TIMESTAMP
                )
            """)

            # C1: raw BKT evidence (enables offline re-simulation of any model)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS evidence_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id    TEXT,
                    username    TEXT,
                    concept     TEXT,
                    tier        TEXT,
                    is_correct  INTEGER,
                    question_id TEXT,
                    ts_utc      TIMESTAMP
                )
            """)

            # C1: per-tier posterior trajectory (append-only snapshots)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bkt_history (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id       TEXT,
                    username       TEXT,
                    concept        TEXT,
                    tier           TEXT,
                    p_tilde        REAL,
                    n_evidence     INTEGER,
                    is_certified   INTEGER,
                    ever_certified INTEGER,
                    trigger        TEXT,
                    ts_utc         TIMESTAMP
                )
            """)

            # C3: model prediction logged BEFORE the outcome (within-subject accuracy)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS prediction_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id   TEXT,
                    username   TEXT,
                    concept    TEXT,
                    tier       TEXT,
                    p_bkt_pred REAL,
                    p_eff_pred REAL,
                    is_correct INTEGER,
                    ts_utc     TIMESTAMP
                )
            """)

            # C3: every slider move + P_G adaptation
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS calibration_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id        TEXT,
                    username        TEXT,
                    concept         TEXT,
                    tier            TEXT,
                    p_bkt_at_time   REAL,
                    p_self          REAL,
                    delta           REAL,
                    direction_ema   REAL,
                    adapted_pg_old  REAL,
                    adapted_pg_new  REAL,
                    ts_utc          TIMESTAMP
                )
            """)

            # SRL: Judgment-of-Learning (confidence vs actual)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS jol_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id        TEXT,
                    username        TEXT,
                    concept         TEXT,
                    confidence_1_5  INTEGER,
                    is_correct      INTEGER,
                    quiz_id         TEXT,
                    ts_utc          TIMESTAMP
                )
            """)

            # C2: every AI turn + adaptation used
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS response_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id        TEXT,
                    username        TEXT,
                    session_id      TEXT,
                    concept         TEXT,
                    intent          TEXT,
                    mastery_level   TEXT,
                    format_sections TEXT,
                    ts_utc          TIMESTAMP
                )
            """)

            # C2: productive-struggle behavioral telemetry
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS behavior_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id    TEXT,
                    username    TEXT,
                    session_id  TEXT,
                    event       TEXT,
                    value       TEXT,
                    message_id  TEXT,
                    ts_utc      TIMESTAMP
                )
            """)

            # SRL: frustration trajectory
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS affect_log (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id          TEXT,
                    username          TEXT,
                    session_id        TEXT,
                    delta_f           REAL,
                    frustration_level TEXT,
                    intervention_fired INTEGER,
                    ts_utc            TIMESTAMP
                )
            """)

            # Ground truth: pre/post test + MAI survey
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS assessment (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id   TEXT,
                    instrument TEXT,
                    item_id    TEXT,
                    bloom_tier TEXT,
                    concept    TEXT,
                    score      REAL,
                    ts_utc     TIMESTAMP
                )
            """)

            # Engagement: session envelope (start/end/duration/turn count)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS session_log (
                    session_id  TEXT PRIMARY KEY,
                    study_id    TEXT,
                    username    TEXT,
                    started_at  TIMESTAMP,
                    ended_at    TIMESTAMP,
                    turn_count  INTEGER DEFAULT 0,
                    user_agent  TEXT
                )
            """)

            # Full conversational record: one row per turn with the complete state vector
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS turn_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id      TEXT,
                    username      TEXT,
                    session_id    TEXT,
                    turn_index    INTEGER,
                    query_text    TEXT,
                    response_text TEXT,
                    intent        TEXT,
                    entities      TEXT,
                    topic         TEXT,
                    mastery_level TEXT,
                    s_goal        REAL,
                    c_code        INTEGER,
                    m_state       TEXT,
                    delta_f       REAL,
                    n_strike      INTEGER,
                    n_sources     INTEGER,
                    latency_ms    INTEGER,
                    was_blocked   INTEGER,
                    block_reason  TEXT,
                    ts_utc        TIMESTAMP
                )
            """)

            # Forethought (SRL): curriculum path adherence vs deviation
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS path_event (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id         TEXT,
                    username         TEXT,
                    concept_asked    TEXT,
                    expected_concept TEXT,
                    deviation_type   TEXT,
                    goal             TEXT,
                    ts_utc           TIMESTAMP
                )
            """)

            # Item-level quiz record (psychometrics: question, answer, timing)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS quiz_log (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id           TEXT,
                    username           TEXT,
                    session_id         TEXT,
                    concept            TEXT,
                    question_text      TEXT,
                    correct_answer     TEXT,
                    student_answer     TEXT,
                    is_correct         INTEGER,
                    confidence_1_5     INTEGER,
                    time_to_answer_sec REAL,
                    is_verification    INTEGER,
                    verification_q_num INTEGER,
                    ts_utc             TIMESTAMP
                )
            """)

            # Learning-from-error: misconception store/resolve events
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS misconception_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id    TEXT,
                    username    TEXT,
                    concept     TEXT,
                    action      TEXT,
                    detail      TEXT,
                    ema_value   REAL,
                    ts_utc      TIMESTAMP
                )
            """)

            # =========================================================
            # CLASSROOM VIDEO — engagement, checkpoints, MCQs, reflections
            # =========================================================

            # Cached LLM-generated checkpoint MCQs (one row per video+checkpoint)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_checkpoint (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_filename  TEXT,
                    checkpoint_time REAL,
                    question        TEXT,
                    options         TEXT,
                    correct_index   INTEGER,
                    concept         TEXT,
                    explanation     TEXT,
                    created_at      TIMESTAMP,
                    UNIQUE(video_filename, checkpoint_time)
                )
            """)

            # Watch engagement events (play/pause/seek/ended/hidden/visible/mute)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_engagement (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id       TEXT,
                    username       TEXT,
                    video_filename TEXT,
                    event          TEXT,
                    position_sec   REAL,
                    detail         TEXT,
                    ts_utc         TIMESTAMP
                )
            """)

            # Per-user watch coverage summary (accumulated watched seconds)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_coverage (
                    study_id        TEXT,
                    username        TEXT,
                    video_filename  TEXT,
                    duration_sec    REAL,
                    watched_sec     REAL DEFAULT 0,
                    completion_pct  REAL DEFAULT 0,
                    hidden_sec      REAL DEFAULT 0,
                    completed       INTEGER DEFAULT 0,
                    updated_at      TIMESTAMP,
                    PRIMARY KEY (username, video_filename)
                )
            """)

            # Checkpoint MCQ responses (also feeds BKT quiz tier)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_mcq_response (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id           TEXT,
                    username           TEXT,
                    video_filename     TEXT,
                    checkpoint_time    REAL,
                    concept            TEXT,
                    selected_index     INTEGER,
                    is_correct         INTEGER,
                    confidence_1_5     INTEGER,
                    time_to_answer_sec REAL,
                    attempts           INTEGER,
                    ts_utc             TIMESTAMP
                )
            """)

            # SRL: pre-video intention + post-video reflection
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_reflection (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id       TEXT,
                    username       TEXT,
                    video_filename TEXT,
                    phase          TEXT,
                    prompt         TEXT,
                    response       TEXT,
                    ts_utc         TIMESTAMP
                )
            """)

            # =========================================================
            # LECTURE CATALOG (flipped-classroom plan)
            # =========================================================
            # The course is organised as Chapters → numbered Videos (see
            # FS2026_CS1050_FlippedClassroomPlan). Before this, videos were whatever
            # .mp4 files happened to sit in the video directory, ordered by filename
            # and bucketed into the 9 skill topics by keyword-matching the filename —
            # so chapter order, slide ranges, and not-yet-recorded videos could not be
            # represented at all.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_chapter (
                    number      INTEGER PRIMARY KEY,   -- 0..14, as printed in the plan
                    title       TEXT NOT NULL,
                    status      TEXT DEFAULT '',       -- e.g. 'SLIDES UPDATED'
                    sort_order  INTEGER,
                    created_at  TIMESTAMP
                )
            """)

            # One row per PLANNED video. `filename` is NULL until a recording is
            # uploaded, so a chapter can list what is still outstanding — the plan
            # itself contains chapters 6-11 and 14 with no videos recorded yet.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS video_catalog (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_number INTEGER NOT NULL,
                    video_number   INTEGER,            -- "Video 14" (may repeat across chapters)
                    title          TEXT NOT NULL,
                    slides         TEXT DEFAULT '',    -- "Slides 1-6"
                    sections       TEXT DEFAULT '',    -- "Section 3.1, 3.2"
                    filename       TEXT,               -- NULL = planned, not yet recorded
                    topic          TEXT DEFAULT '',    -- skill-graph topic, for cross-linking
                    status         TEXT DEFAULT 'planned',  -- planned | complete
                    sort_order     INTEGER,
                    created_at     TIMESTAMP,
                    updated_at     TIMESTAMP,
                    FOREIGN KEY (chapter_number) REFERENCES video_chapter(number)
                )
            """)
            # kind: 'lecture' for the numbered plan videos, 'supplement' for the
            # extra material a chapter accumulates (review sessions, worked
            # examples, guest recordings). Supplements carry no video_number —
            # they are not part of the numbered sequence and there can be any
            # number of them per chapter.
            try:
                self.conn.execute(
                    "ALTER TABLE video_catalog ADD COLUMN kind TEXT DEFAULT 'lecture'")
            except sqlite3.OperationalError:
                pass  # column already exists

            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_catalog_chapter "
                "ON video_catalog(chapter_number, sort_order)"
            )
            # A recording belongs to exactly one catalog slot.
            self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_filename "
                "ON video_catalog(filename) WHERE filename IS NOT NULL"
            )

            # Prerequisite-coupled priors ("head start"): every seed + clawback event.
            # Lets us prove in analysis that a head start never certified a topic.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS headstart_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id    TEXT,
                    username    TEXT,
                    concept     TEXT,
                    action      TEXT,
                    hs_quiz     REAL,
                    hs_micro    REAL,
                    hs_code     REAL,
                    seed_quiz   REAL,
                    seed_micro  REAL,
                    seed_code   REAL,
                    sources     TEXT,
                    ts_utc      TIMESTAMP
                )
            """)

            # Motivational self-report (Tier 2: self-efficacy, interest, goal orientation)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS self_report (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id   TEXT,
                    username   TEXT,
                    dimension  TEXT,
                    item_id    TEXT,
                    score      REAL,
                    ts_utc     TIMESTAMP
                )
            """)

            # SRL: Metacognitive Calibration Network verdicts (append-only)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS mcn_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id      TEXT,
                    username      TEXT,
                    concept       TEXT,
                    map_C         TEXT,
                    map_K         TEXT,
                    confidence    REAL,
                    n_signals     INTEGER,
                    p_over        REAL,
                    p_cal         REAL,
                    p_under       REAL,
                    evidence_json TEXT,
                    ts_utc        TIMESTAMP
                )
            """)

            # Auto-captured prelab work. A guided-practice run that reaches the
            # last step IS the submission — the student never uploads anything,
            # so the grader needs the whole conversation, not just final code.
            # UNIQUE(username, video_filename, prelab_prompt) makes a re-attempt
            # overwrite rather than accumulate: the row is the student's current
            # standing on that prelab, and attempt_count records the retries.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS prelab_submission (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    username       TEXT NOT NULL,
                    video_filename TEXT,
                    video_title    TEXT,
                    chapter_title  TEXT,
                    prelab_prompt  TEXT NOT NULL,
                    session_id     TEXT,
                    status         TEXT,      -- 'solved' | 'reflected'
                    steps_total    INTEGER,
                    steps_passed   INTEGER,
                    total_fails    INTEGER,
                    final_code     TEXT,      -- last C block the student submitted
                    reflection     TEXT,      -- their own summary, when they write it
                    transcript     TEXT,      -- full chat history, JSON
                    grade_status   TEXT,      -- 'pass' | 'values_differ' | 'wording_differs'
                                              -- | 'compile_error' | 'no_code' | 'not_gradable'
                    grade_detail   TEXT,      -- JSON: the failing lines, or the compiler error
                    flags          TEXT,      -- JSON: integrity signals worth a second look
                    teacher_score  REAL,      -- the instructor's own mark, when they set one
                    teacher_note   TEXT,
                    attempt_count  INTEGER DEFAULT 1,
                    solved_at      TIMESTAMP,
                    updated_at     TIMESTAMP,
                    UNIQUE(username, video_filename, prelab_prompt)
                )
            """)

            # The grading columns arrived after the table, and a database created
            # in between has the table without them — CREATE TABLE IF NOT EXISTS
            # will not add a column to a table that already exists.
            for col, definition in [
                ("grade_status",  "TEXT"),
                ("grade_detail",  "TEXT"),
                ("flags",         "TEXT"),
                ("teacher_score", "REAL"),
                ("teacher_note",  "TEXT"),
            ]:
                try:
                    self.conn.execute(
                        f"ALTER TABLE prelab_submission ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Safe catch to add the column to existing databases without breaking.
            # NOTE: do NOT re-import sqlite3 here. It is already imported at module
            # level, and a function-local `import sqlite3` rebinds the name for the
            # WHOLE function scope — so every `except sqlite3.OperationalError`
            # ABOVE this line raises UnboundLocalError instead of being caught.
            # That failure is invisible until an ALTER actually raises, i.e. the
            # second time the app starts against the same database.
            try:
                self.conn.execute("ALTER TABLE user_feedback ADD COLUMN feedback_text TEXT")
            except sqlite3.OperationalError:
                pass # Column already exists
                
            try:
                self.conn.execute("ALTER TABLE messages ADD COLUMN action_taken TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                self.conn.execute("ALTER TABLE messages ADD COLUMN style_used TEXT")
            except sqlite3.OperationalError:
                pass

            # Tier 3: academic emotion label on the affect time-series
            try:
                self.conn.execute("ALTER TABLE affect_log ADD COLUMN academic_emotion TEXT")
            except sqlite3.OperationalError:
                pass

            # Which evidence tier was lagging when the adaptation level was chosen.
            # Without this the assigned level is observable but the diversity signal
            # that produced it is not, so tier-steering cannot be audited.
            try:
                self.conn.execute("ALTER TABLE response_log ADD COLUMN weak_tier TEXT")
            except sqlite3.OperationalError:
                pass

            # Per-turn multi-turn risk (Eqs. 9-11) on EVERY turn, not only blocks.
            # Required for escalation recall and any tau_judge sweep; without it the
            # trajectory layer's contribution is not directly measurable.
            for _col in ("traj_risk", "traj_acc", "traj_peak"):
                try:
                    self.conn.execute(f"ALTER TABLE turn_log ADD COLUMN {_col} REAL")
                except sqlite3.OperationalError:
                    pass

            # Reparameterization migration: rows seeded under the old zero-prior
            # scheme have p_mastery_micro=0.0 or p_mastery_code=0.0 but no
            # recorded interactions (any actual interaction would push them above 0.09).
            # Update those rows to the new Cromwell-safe priors.
            try:
                self.conn.execute(
                    "UPDATE user_knowledge SET p_mastery_micro=0.05 WHERE p_mastery_micro=0.0"
                )
                self.conn.execute(
                    "UPDATE user_knowledge SET p_mastery_code=0.01 WHERE p_mastery_code=0.0"
                )
            except Exception:
                pass

            # SM-2 spaced repetition + BKT columns on user_knowledge
            for col, definition in [
                ("interval_days",   "INTEGER DEFAULT 1"),
                ("ease_factor",     "REAL DEFAULT 2.5"),
                ("due_date",        "TIMESTAMP"),
                ("review_count",    "INTEGER DEFAULT 0"),
                ("p_mastery",       "REAL DEFAULT 0.3"),
                ("p_mastery_quiz",  "REAL DEFAULT 0.3"),
                ("p_mastery_micro", "REAL DEFAULT 0.05"),
                ("p_mastery_code",  "REAL DEFAULT 0.01"),
                # Hysteresis flag: 1 once all tiers cross THETA_CERTIFY (0.95),
                # 0 if any tier later drops below THETA_DECERTIFY (0.75).
                ("is_certified",    "INTEGER DEFAULT 0"),
                # Per-tier last-interaction timestamps for forgetting-augmented BKT decay.
                # NULL until the first interaction on that tier.
                ("last_quiz_at",    "TIMESTAMP"),
                ("last_micro_at",   "TIMESTAMP"),
                ("last_code_at",    "TIMESTAMP"),
                # Explicit evidence counts — certification requires n_evidence^(k) >= 3.
                # Incremented only on correct answers (Fix #3: n_min explicit gate).
                ("n_evidence_quiz",  "INTEGER DEFAULT 0"),
                ("n_evidence_micro", "INTEGER DEFAULT 0"),
                ("n_evidence_code",  "INTEGER DEFAULT 0"),
                # ever_certified: 1 once all tiers have been jointly certified at any point.
                # Never reset to 0. Prerequisite gate uses this (not current decayed P̃)
                # so forgetting-decay cannot re-lock earned prerequisites (Fix #5).
                ("ever_certified",   "INTEGER DEFAULT 0"),
                # Per-tier adaptive decay rates λ (day^-1).
                # Initialized from DECAY_RATES; each correct review divides by ease_factor
                # to slow forgetting — SM-2/BKT coupling (Fix #6).
                ("decay_quiz_lam",   "REAL DEFAULT 0.04951"),
                ("decay_micro_lam",  "REAL DEFAULT 0.09902"),
                ("decay_code_lam",   "REAL DEFAULT 0.13863"),
                # Prerequisite-coupled priors ("head start"). The unearned portion
                # of each tier's seeded prior, carried from certified prerequisites.
                # Stored separately so it is identifiable (it never counts as evidence,
                # so it can never certify) and clawback-able while n_evidence is still 0.
                ("hs_quiz",          "REAL DEFAULT 0"),
                ("hs_micro",         "REAL DEFAULT 0"),
                ("hs_code",          "REAL DEFAULT 0"),
                ("head_start_at",    "TIMESTAMP"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE user_knowledge ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError:
                    pass # Column already exists
            
    def execute(self, query, params=()):
        """Execute a write operation"""
        with self.conn:
            return self.conn.execute(query, params)

    def fetch_one(self, query, params=()):
        """Get a single row"""
        cursor = self.conn.execute(query, params)
        return cursor.fetchone()

    def fetch_all(self, query, params=()):
        """Get all rows"""
        cursor = self.conn.execute(query, params)
        return cursor.fetchall()

# Singleton Instance
db = SQLiteDB()