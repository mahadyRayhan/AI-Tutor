# SAGE Security Work — Checklist (implementation + testing)

Companion to `SECURITY_EVALUATION_PLAN.md`. Checked = done this session.

---

## 1. Implementation

### 1.1 Done this session
- [x] **Task 1 — Revocable certification.** `bkt.is_current_certified()` single source of truth; rewired the 4 behaviour consumers (`has_mastered`, `get_known_concepts`, `_classify_mastery_level` REVIEWING path, `_prereq_strength`).
- [x] **Task 2 — Ablation switches.** `config.py` flags (`session_monitor`, `judge_escalation`, `context_gating`, `earned_credentials`); single run param `SAGE_ABLATE`; per-component env override; wired into Sentinel; per-session `ablation_config` audit event.
- [x] **Task 3 — Hardened adjudicator.** Delimited + data-labelled transcript; fixed JSON verdict schema; unparseable → fail; timeout + fail-closed.

### 1.2 To build — Consistency detector (flagship)
- [ ] **`competence_consistency.py`** module (mirror the `mcn.py` pattern: dependency-light, flag-gated **off by default**, returns `None` on thin evidence).
- [ ] Consume **assessed observations only** (from `prediction_log`).
- [ ] Apply the **emission transform**: `p_bkt_pred` (P̃) → `P(correct) = P̃(1−P_S) + (1−P̃)P_G` using each tier's guess/slip.
- [ ] Use **`p_bkt_pred`, not `p_eff_pred`** (self-assessment must not move the flag).
- [ ] **e-process / test-martingale** (Ville bound), **not** split conformal. Directional betting on **tier-imbalance** (over-performance concentrated in the applied/code tier).
- [ ] Wealth accumulator; flag when it exceeds `1/α`.
- [ ] Config flag `consistency_detector_enabled` (default off).
- [ ] Hook **after** `bkt.update()` writes `prediction_log`.
- [ ] Output a flag **event only** — **never** write to `user_knowledge`, certification, or access.

### 1.3 To build — review surface + tightening
- [ ] **Instructor review queue** in the teacher dashboard (the landing spot for flags; without it the detector is invisible).
- [ ] *(Optional)* **Latitude revocation tightening** — route the `S_goal` cert-count through `is_current_certified` (close the lazy-reconciliation gap).

### 1.4 Paper-only (no code)
- [ ] Name **earned latitude** as a reinterpretation (already deployed, recolour to journal-extension).
- [ ] State the **hard-block precedence invariant** with the code-path citation.
- [ ] Write the **umbrella thesis** (pedagogical machinery = security machinery).
- [ ] Add a **Datasets** subsection (new vs reused; size/license/access).

---

## 2. Corpora & harness (prerequisite for evaluation)
- [ ] **A1 self-authored** attack corpus (credential inflation).
- [ ] **A2 self-authored** attack corpus (asset extraction).
- [ ] **PyRIT** wiring for **A3** (harm-proxy generation).
- [ ] **XSTest** pulled in for the benign over-block control (E4).
- [ ] **A4 synthetic anomaly** harness (splice expert-tier answers into novice trajectories).
- [ ] Domain benign set ("why is my loop infinite?", "what does fork() do?").

---

## 3. Testing & evaluation

### 3.1 Done this session
- [x] `tests/test_ablation_security.py` — 23 tests (Task 1 revocation, Task 2 flags + behaviour, Task 3 hardening).
- [x] Fail-before demonstrated (decayed cert passed; judge failed open) → both fixed.
- [x] Full suite green (49 passed; 1 pre-existing unrelated error).

### 3.2 Unit / property tests to add
- [ ] **B1** — splice expert applied-tier answer into a novice trajectory → detector **flags**.
- [ ] **B2** — genuine fast learner (uniform gain across tiers) → **does not flag**.
- [ ] **B3** — replay real trajectories → **FPR ≤ α over the whole sequence** (Ville).
- [ ] **B4** — detector is **side-effect-free** (no posterior / cert / access change when enabled).
- [ ] **B5** — flag is **self-assessment-immune** (uses `p_bkt_pred`, not `p_eff`).
- [ ] **B6** — **hard-block invariant**: max-certification user still blocked on **all** Layer-1a signatures.

### 3.3 Evaluation tracks (run order)
- [ ] **E1 — Ablation** ★ · **RUN FIRST, ACCEPT RESULT** (ΔASR, ΔFPR per component).
- [ ] **E2 — Red-team per adversary** (ASR + layer attribution; A1/A2 self · A3 PyRIT · A4 self).
- [ ] **E3 — Crescendo** — re-confirm under the hardened judge (already run once).
- [ ] **E4 — Usability / over-block** (XSTest; report beside every block rate).
- [ ] **E5 — Adjudicator hardening** — injection suite + timeout/error/garbage fault injection.
- [ ] **E6 — Revocable + latitude** — Task 1 revocation + `turn_log` flip-zone count.
- [ ] **E7 — Detector eval** — B1–B5 formalised (detection rate, FPR, ROC-AUC, ARL).
- [ ] **E8 — Structural invariants** — B6 + latitude floor + detector side-effect-free.
- [ ] **E9 — Determinism & auditability** — reproducibility %, audit completeness %.
- [ ] **E10 — Adaptive adversary** — optional (slow-burn under threshold, gamed self-assessment).

---

## 4. Suggested order

1. **Now (read-only, no risk):** E6 `turn_log` latitude check → decides if latitude stays a headline. Add **B6** invariant test.
2. **Corpora:** §2 (A1/A2 self-authored, PyRIT, XSTest, A4 harness).
3. **E1 ablation first**, then **E2 + E4 together**.
4. **E5, E9** (extend existing tests).
5. **Build the detector** (§1.2) + **review surface** (§1.3), then **B1–B5 / E7**.
6. **E10** last (optional).
