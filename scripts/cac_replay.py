#!/usr/bin/env python3
"""Replay Cognitive Access Control over historical turns.

WHY THIS EXISTS
───────────────
The branch is not deployed, so there is no live traffic to observe. Every number
about CAC before deployment has to come from replaying the policy against turns
that already happened. That is possible only because `cac_graph.decide()` is a
pure function of (topology, learner view, query) — no database, no model, no
mutation — so it can be re-run offline with any switch configuration and produce
exactly the decision the live path would have produced.

Nothing is written. The script only reads.

    # what would CAC have done, with everything connected?
    python scripts/cac_replay.py

    # attribute one signal: run with it disconnected and diff
    python scripts/cac_replay.py --signals cac_rung,cac_horizon
    python scripts/cac_replay.py --off cac_horizon

    # per-learner detail for the students it would have touched
    python scripts/cac_replay.py --detail 20

STATE RECONSTRUCTION — read this before quoting a number
────────────────────────────────────────────────────────
A learner's certified set changes over the term, so replaying a July turn against
a September certification would credit the learner with knowledge they did not
have, and would under-report how often CAC fires.

    --state asof     (default) certification as of each turn's timestamp,
                     reconstructed from bkt_history. Correct, but only as
                     complete as that table.
    --state current  today's certification for every turn. Faster, and wrong in
                     a known direction: it OVER-states K, so it UNDER-states how
                     often CAC would have acted. A floor, not an estimate.
    --state none     nobody has certified anything. The other bound: the most
                     restrictive reading the policy can produce.

The report prints how many turns had any reconstructable state, because the
answer on this corpus is "not many", and a rate computed over 3448 turns when
only a fraction have real state behind them is a rate about the dataset rather
than about the policy.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("SAGE_BASE_URL", "http://localhost:8000")

from app.core import cac_graph                       # noqa: E402
from app.core.cac_graph import CURRICULUM, LearnerView, Rung, decide  # noqa: E402
from app.core.concept_canon import canonical_concept  # noqa: E402
from app.db.sqlite_db import db                       # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# Certification state
# ══════════════════════════════════════════════════════════════════════════

def certification_timeline() -> dict[str, list[tuple[str, str, bool]]]:
    """username -> [(ts, concept, certified)], oldest first.

    Read from bkt_history, which records a row whenever certification changed.
    """
    rows = db.fetch_all(
        "SELECT username, concept, is_certified, ts_utc FROM bkt_history "
        "WHERE ts_utc IS NOT NULL ORDER BY ts_utc ASC")
    timeline: dict[str, list] = defaultdict(list)
    for r in rows:
        timeline[r["username"]].append(
            (str(r["ts_utc"]), canonical_concept(r["concept"]), bool(r["is_certified"])))
    return timeline


def certified_asof(events: list[tuple[str, str, bool]], ts: str) -> frozenset[str]:
    """Replay certification events up to `ts`. Later events are not visible."""
    held: set[str] = set()
    for ev_ts, concept, certified in events:
        if ev_ts > ts:
            break
        if concept not in CURRICULUM.nodes:
            continue
        held.add(concept) if certified else held.discard(concept)
    return frozenset(held)


def certified_current(username: str) -> frozenset[str]:
    from app.core import bkt_model
    held = set()
    for topic in CURRICULUM.nodes:
        try:
            if bkt_model.is_current_certified(username, topic):
                held.add(topic)
        except Exception:
            pass
    return frozenset(held)


# ══════════════════════════════════════════════════════════════════════════
# Replay
# ══════════════════════════════════════════════════════════════════════════

# Alternative readings of "earned enough to move on". The frontier definition —
# not the rung — is what decides how often CAC fires, so it is swept separately.
FRONTIER_SQL = {
    "ever":    "SELECT username, concept FROM user_knowledge WHERE ever_certified=1",
    "halfway": "SELECT username, concept FROM user_knowledge WHERE COALESCE(p_mastery,0) >= 0.5",
    "touched": "SELECT username, concept FROM user_knowledge "
               "WHERE COALESCE(n_evidence_quiz,0)+COALESCE(n_evidence_micro,0)"
               "     +COALESCE(n_evidence_code,0) > 0",
}


def earned_sets(basis: str) -> dict[str, frozenset[str]]:
    """username -> concepts counted as earned, under one reading."""
    from collections import defaultdict as _dd
    out = _dd(set)
    for r in db.fetch_all(FRONTIER_SQL[basis]):
        c = canonical_concept(r["concept"])
        if c in CURRICULUM.nodes:
            out[r["username"]].add(c)
    return {u: frozenset(v) for u, v in out.items()}


def replay(signals: set[str], state_mode: str, limit: int | None,
           region_gate: bool = True, frontier_basis: str = "certified"):
    turns = db.fetch_all(
        "SELECT username, session_id, topic, ts_utc, intent, was_blocked "
        "FROM turn_log WHERE topic IS NOT NULL AND topic != 'General' "
        "ORDER BY ts_utc ASC" + (f" LIMIT {int(limit)}" if limit else ""))

    timeline = certification_timeline() if state_mode == "asof" else {}
    current_cache: dict[str, frozenset[str]] = {}
    earned = earned_sets(frontier_basis) if frontier_basis != "certified" else None

    stats = Counter()
    redirects = Counter()
    rungs = Counter()
    per_user = defaultdict(lambda: Counter())
    off_graph = Counter()

    for t in turns:
        user, topic = t["username"], canonical_concept(t["topic"])
        if topic not in CURRICULUM.nodes:
            off_graph[t["topic"]] += 1
            continue
        stats["turns"] += 1

        if earned is not None:
            certified = earned.get(user, frozenset())
        elif state_mode == "none":
            certified = frozenset()
        elif state_mode == "current":
            if user not in current_cache:
                current_cache[user] = certified_current(user)
            certified = current_cache[user]
        else:
            events = timeline.get(user, [])
            if events:
                stats["turns_with_state"] += 1
            certified = certified_asof(events, str(t["ts_utc"]))

        if certified:
            stats["turns_with_certification"] += 1

        d = decide(LearnerView(user, certified), [topic], signals=signals,
                   region_gate=region_gate)
        if d.beyond_region:
            stats["beyond_region"] += 1
        rungs[d.rung_cap.label] += 1
        if d.is_permissive:
            stats["in_region"] += 1
            per_user[user]["ok"] += 1
        else:
            stats["would_act"] += 1
            per_user[user]["acted"] += 1
            if d.redirect_to:
                redirects[f"{topic} -> {d.redirect_to}"] += 1

    return stats, redirects, rungs, per_user, off_graph


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signals", help="comma-separated signal switches to connect "
                                      "(default: all implemented)")
    ap.add_argument("--off", help="comma-separated signal switches to disconnect")
    ap.add_argument("--state", choices=("asof", "current", "none"), default="asof")
    ap.add_argument("--advisory", action="store_true",
                    help="do NOT cap beyond-frontier asks; annotate only "
                         "(the gate is on by default)")
    ap.add_argument("--frontier", choices=("certified", "ever", "halfway", "touched"),
                    default="certified",
                    help="what counts as earned when computing the frontier. "
                         "This is what decides how OFTEN CAC fires.")
    ap.add_argument("--limit", type=int, help="replay only the first N turns")
    ap.add_argument("--detail", type=int, default=0,
                    help="show the N learners CAC would have acted on most")
    a = ap.parse_args()

    all_signals = set(cac_graph.SIGNAL_SWITCHES)
    signals = set(a.signals.split(",")) if a.signals else set(all_signals)
    if a.off:
        signals -= set(a.off.split(","))
    unknown = signals - all_signals
    if unknown:
        print(f"unknown signal(s): {', '.join(sorted(unknown))}")
        print(f"known: {', '.join(sorted(all_signals))}")
        return 2

    implemented = [n for n in sorted(all_signals)]
    print("CAC replay")
    print(f"  signals connected : {', '.join(sorted(signals)) or '(none)'}")
    print(f"  state             : {a.state}")
    print(f"  region gate       : {'advisory (annotate only)' if a.advisory else 'CAPS at ORIENT (default)'}")
    print(f"  frontier basis    : {a.frontier}")
    print(f"  note              : all signal hooks are still stubs, so a connected "
          f"signal\n                      contributes nothing yet — the K-region "
          f"baseline is what\n                      this measures until Phase 2 lands.")
    print()

    stats, redirects, rungs, per_user, off_graph = replay(
        signals, a.state, a.limit, not a.advisory, a.frontier)

    n = stats["turns"]
    if not n:
        print("No replayable turns found.")
        return 1

    print(f"  turns replayed            {n}")
    if a.state == "asof":
        tws = stats["turns_with_state"]
        print(f"  turns with any history    {tws}  ({100*tws/n:.1f}%)"
              f"   <- coverage of bkt_history")
    twc = stats["turns_with_certification"]
    print(f"  turns with certification  {twc}  ({100*twc/n:.1f}%)")
    print()
    br = stats["beyond_region"]
    print(f"  beyond the frontier       {br}  ({100*br/n:.1f}%)   <- observed either way")
    print(f"  unchanged for the learner {stats['in_region']}  ({100*stats['in_region']/n:.1f}%)")
    print(f"  CAC would have acted      {stats['would_act']}  ({100*stats['would_act']/n:.1f}%)")
    print()

    print("  disclosure rung:")
    for label, c in rungs.most_common():
        print(f"    {label:16} {c:6}  ({100*c/n:.1f}%)")

    if redirects:
        print("\n  most common redirects (asked -> missing prerequisite):")
        for k, c in redirects.most_common(8):
            print(f"    {k:34} {c}")

    if off_graph:
        print("\n  topics outside the coarse graph (CAC has no opinion):")
        for k, c in off_graph.most_common(5):
            print(f"    {k:24} {c}")

    if a.detail:
        print(f"\n  learners CAC would have acted on most:")
        ranked = sorted(per_user.items(), key=lambda kv: -kv[1]["acted"])[:a.detail]
        for user, c in ranked:
            total = c["ok"] + c["acted"]
            print(f"    {user[:28]:30} {c['acted']:4}/{total:<4} "
                  f"({100*c['acted']/total:.0f}% of their turns)")

    if twc == 0:
        print("\n  WARNING: no turn had any certified concept, so every decision here "
              "\n  is the cold-start case. This measures the corpus, not the policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
