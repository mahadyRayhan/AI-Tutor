# Teacher Dashboard — Feature Roadmap & Status

Tracks the redesign suggestions against what is actually built. Last updated: 2026-08-09.

**Status legend:** ✅ Done · 🟡 Partial / dormant · ⬜ Not started
**Data readiness:** `data-ready` (table + rows exist) · `dormant` (wired but ~no rows yet) · `needs-logging` (instrumentation missing)

---

## ✅ Implemented

| Feature | Notes |
|---|---|
| **Merged Class Progress Monitor** | Risk Matrix + Progress fused into one searchable/sortable table with a shared period selector (1d/1wk/1mo/All). |
| **Tier mastery plot** (was: cluster scatter) | 2-D scatter on the **real 3-tier BKT**: X = declarative (quiz) × Y = applied (code), k-means groups, parity diagonal exposes tier imbalance. Rewired from the heuristic 2026-08-09. |
| **Action Triage (landing view)** | One row per learner, ONE named mechanism + one-line derivation + suggested action, from `user_knowledge` using the model's own thresholds (θ_dec=0.75, N_min=3). No composite score. |
| **Model Health panel** | Brier (raw BKT vs effective) + reliability curve from `prediction_log`. Ships 2026-08-10. Immediately surfaced a real low-end miscalibration (predicts ~3%, observed ~98%). |
| **Security Ledger** | Aggregate gated turns from `turn_log`: evasion (587 `SECURITY_RISK`, 290 learners, peak risk 0.80) separated from pedagogical redirects (1017). No learners named. Ships 2026-08-10. |
| **What to prepare next + group assignment** | NEXT-tab prep list: one prep action per topic (re-explain / coding session / first exposure / reinforce / on track) from `_class_tier_matrix()`, ranked by urgency then group size. Each row is an assignable group — the students weak on that topic/tier — with topic-matched reading/code from `resources/` (`_TOPIC_RESOURCES`). "Assign to all N" POSTs `/assign_group`, which (a) delivers to each student via `assignment_manager.create_material_assignment` and (b) logs one `instructor_action` per student to `event_log`. Teacher can pick a curriculum file **or upload their own** (`/upload_material` → `resources/assignments/`). Ships 2026-08-11. Dry-run: Arrays→re-explain (10), Variables→reinforce (43), Strings/Structures→first exposure (52). |
| **Student-side delivery of assignments** | Assigned material surfaces in the student dashboard's existing **Active Challenges** panel as a resource card (📄/💻/📎 Open + Mark-as-done), served path-safe via `/materials/download`. Routed through the existing `assignments` table (lazily extended with `kind/material/material_kind/topic`; old challenge rows default `kind=challenge`). Ships 2026-08-11. Verified on a DB copy: migrate → insert → round-trip → mark-done. |
| **Assignment follow-through ("Did assignments land?")** | NEXT-tab panel closing the loop: each sent assignment (grouped by topic + material + day) shows done/total, a progress bar, and — on expand — which students haven't opened it. Endpoint `/assignment_status` (pure read on `assignments`, ASSIGNED vs DONE). Refreshes right after an assign. Ships 2026-08-11. |
| **Tier heatmap (CLASS)** | Topic × (explain/complete/write) grid, whole class at a glance — graded red→amber→green cells make the explain→write fade ("knows it, can't code it") pop across all topics at once, complementing the per-topic standing table. Pure frontend on the cached `class_standing` data (no new endpoint/fetch). Greys cells on the **same** "not enough data" rule as the table (<3 practised or <15% of class) so the two panels never disagree. Ships 2026-08-11. |
| **In-chat assignment nudge** | Dismissable banner at the top of the student **chat** (`index.html`), so assignments are seen where students actually work, not only on the analytics dashboard. Reads the existing `/assignments/student/{username}` (filters `kind=material`, not DONE); each item is an Open link (download) + ✕ mark-done → `/assignments/material_done`, feeding the follow-through panel. Loaded from `initializeApp()`; strictly additive — no change to chat/tutor logic. Ships 2026-08-11. |

### Triage mechanisms (inside the Triage panel)
| Mechanism | Status | Data |
|---|---|---|
| Tier imbalance ("defines but can't use") | ✅ fires today | data-ready |
| Stalled (many attempts, flat posterior) | 🟡 proxy logic in; fires with data | data-ready |
| Decertified (`ever_certified & !is_certified`) | ✅ fires today | data-ready (reconcile sweep clears stale certs first, 2026-08-10) |
| Certified on minimum evidence (N = N_min) | 🟡 wired, not firing | dormant |
| Active misconception (M_c ≥ 0.25) | ⬜ | dormant (`misconception_log` ~1 row) |
| Gate friction (repeated prereq block) | ⬜ | blocked — `path_event.deviation_type` all `'unknown'` |
| Elevated cognitive load (Eq. 19) | ⬜ | needs-logging |

---

## ⬜ Not started — core suggestions

| # | Feature | Data readiness | Notes |
|---|---|---|---|
| 2 | **Tier heatmap over concept map** (3 cells/topic, cohort) | data-ready (`user_knowledge` tiers) | Replaces current single-mastery viz; unlocks the "malloc known, not usable" glance. |
| 2 | **Neo4j DAG overlay + gate pressure** (# blocked downstream) | partial | Graph exists; `path_event` deviation type not populated → gate pressure needs that fixed first. |
| 3 | **Session summaries** (intent dist, help-seeking, struggle) + transcript behind logged expand | needs-logging (access log) | Student modal shows raw history today, not the meta-reflective summary. IRB/privacy angle. |
| 4 | **Model Health panel** (Brier, reliability curve, deferral rate) | **data-ready** (`prediction_log`, 85 rows) | Turns the calibration reviewer-objection into a feature. Self-contained. |
| 5 | **Security ledger** (aggregate by layer/reason; boundary vs evasion) | **data-ready** (`turn_log.was_blocked/block_reason`) | Must separate `SECURITY_RISK` (evasion) from pedagogical reasons; aggregate-first (harm to lead with "who jailbroke"). |

## ⬜ Not started — advisor additions

| Feature | Data readiness | Notes |
|---|---|---|
| **Decertification forecast** (who lapses in 7/14 days) | ✅ built 2026-08-10 — **empty on current data** | Panel + endpoint `decert_forecast` ship; projects the forgetting curve (`T* = −ln((θ−p₀)/(p̃−p₀))/λ`) per tier, soonest-crossing tier drives the lapse date, reconciles first. **Renders 0 now**: the only 2 certified learners (`fB305_*`) have NULL `last_*_at`, and `_apply_decay` doesn't decay timestamp-less rows — so they never lapse. Honest empty state explains this. Populates once learners have dated practice. |
| **Counterfactual preview on actions** (blast radius before unlock/decertify) | data-ready (Neo4j) | Removes fear of dashboard controls. |
| **Class metacognitive disagreement** (aggregate self-downgrade by topic) | dormant (`calibration_log` 2 rows) | **SRL paper only.** |
| **Auto warm-up from misconception clusters** | dormant (`misconception_log`) | Dashboard produces an artifact, not just a display. |
| ~~**Log instructor actions to append-only stream**~~ | ✅ done 2026-08-10 | Modal action bar (Assign task / Unlock prerequisite / Force review), suggested action highlighted from the learner's flag; every press POSTs to `/log_action` → `event_log` as `instructor_action`. Effects layered later; the record is the point. |
| ~~**Tier trajectory chart**~~ | ✅ done 2026-08-10 | Per-concept multi-line chart (quiz/micro/code over time) + cert/decert markers + θ_dec line, from `bkt_history` (pure read). In the student modal. |
| ~~**Session summaries / tagged history**~~ | ✅ done 2026-08-10 | Modal interaction history now shows per-session tags (dominant topic, intent mix, turns, ⚠ struggled) from `turn_log` instead of truncated query strings. Transcript still behind expand (logged-access on expand = minor TODO). |

---

## ⚠️ Debt & anti-pattern compliance ("things not to do")

| Item | Status | Action needed |
|---|---|---|
| Heuristic vs real model | ✅ resolved 2026-08-10 | Tier plot + Triage (2026-08-09) and now the **student-modal mastery bars** all use the real 3-tier BKT. Modal fetches per-concept decayed posteriors from `student_detail` (reconciles the learner's stale certs first), shows quiz/micro/code + a ✓/⚠️ lapsed badge that matches Triage. `_calculate_mastery` no longer feeds any dashboard viz. |
| No composite score w/o decomposition | ✅ resolved 2026-08-10 | Triage complies; the **student modal** now shows 3 per-tier bars with N^(k) colored by the weakest tier (was a compensatory composite reading "59%" green for quiz 91%/code 1%). Merged table shows a plain % + Reason column (uncolored — no green/red lie). |
| **"You mastered X" gate** (learner-facing) | ✅ fixed 2026-08-10 | `get_known_concepts` returned EVERY touched concept, not certified ones — the tutor congratulated mastery for topics at code tier 1%/n=0. Now filters `ever_certified=1`. Verified: TEST12 touched 11, certified 0 → now 0 "known". |
| **Triage read pre-decay posteriors** | ✅ fixed 2026-08-10 | Triage `evaluate()` read raw `p_mastery_*` while every other view decays. Caused the 0.98(triage) vs 0.912(modal) disagreement. Now decay-adjusts all three tiers. |
| Don't show affect per-learner as fact | ✅ resolved 2026-08-10 | Per-student Frustration column removed; affect now surfaces as a **cohort chip** ("N% of active learners reading elevated") in the summary strip, labeled as an estimate. |
| Don't rank learners against each other | 🟡 | Triage-by-need is fine; avoid any leaderboard framing. |
| ≤ ~5 elements on landing | 🟡 | Triage card is clean; overall dashboard has many sections — landing discipline not enforced. |

---

## 📄 Research / paper (not code)

- Keep the **IRL vs SRL boundary** clean per panel (triage/heatmap/gate/decert/model-health/security = IRL; metacognitive disagreement/slider = SRL).
- **Dashboard-accuracy measurement:** show triage, have instructors name top-3 + intervention, score vs held-out ground truth. Stronger than a Likert usability item. — protocol not yet designed.

---

## Suggested next order (value × data-readiness)

1. ~~Rewire mastery viz to the real 3-tier BKT~~ ✅ **done 2026-08-09** (Tier plot: quiz × code).
2. ~~Model Health panel~~ ✅ **done 2026-08-10** (Brier + reliability curve).
3. ~~Security ledger~~ ✅ **done 2026-08-10** (evasion vs curriculum boundary).
4. ~~Fix the stale `is_certified` bug~~ ✅ **done 2026-08-10** — added `reconcile_certifications()` (bkt_model), a decertify-only sweep that applies `is_mastered()`'s own hysteresis eagerly over all certified rows; the Triage endpoint runs it before reading the flag. Verified on a DB copy: 25 certified → **13 decertified** → 12 remain, idempotent, `ever_certified` preserved. Those 13 now surface as **Decertified** in Triage instead of being skipped/mislabeled.
5. ~~Decertification forecast~~ ✅ **built 2026-08-10** — panel + `decert_forecast` endpoint ship and are correct. **Empty on current data**: the only 2 certified learners have NULL `last_*_at`, so the model never decays them (`_apply_decay` no-ops without a timestamp). Honest empty state explains it; populates with dated practice. NOTE: earlier "12 lapse in 7 days" estimate was an artifact of mishandling NULL timestamps in a dry-run — corrected.
6. Fix the affect-column anti-pattern (per-student Frustration) in the merged table.
6. Later / after data collection: misconception warm-up, gate pressure (needs deviation_type fix), SRL disagreement, session summaries, instructor-action logging.
