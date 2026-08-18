#!/usr/bin/env python3
"""One-time migration: merge CASE/PLURAL concept-name variants in `user_knowledge`
onto their canonical coarse-topic name (see app/core/concept_canon.py).

CONSERVATIVE by construction: a row is touched ONLY when its concept is a case/plural
variant of one of the nine coarse topics (is_canonical_topic == True) and it isn't
already stored under the canonical spelling, or several such variants coexist for one
user. Fine curriculum nodes ("Variables and Types", "malloc"), sentinel names
("code submission"), and any unknown concept are LEFT UNTOUCHED — so evaluation
cohorts with fine-grained rows are unaffected.

Merge rule (per user, per canonical topic): keep one survivor row, fold the others in
— posteriors = max, evidence = sum, timestamps = latest, decay λ = min (most
practised), cert flags = OR — then delete the folded rows and rename the survivor.

Usage:
  python migrate_concept_canon.py            # DRY RUN — report only
  python migrate_concept_canon.py --apply    # back up DB first, then migrate
"""
import sys, os, shutil, sqlite3, datetime
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.core.concept_canon import canonical_concept, is_canonical_topic

DB = os.path.join(os.path.dirname(__file__), "..", "backend", "database", "ai_tutor.db")
APPLY = "--apply" in sys.argv
TIERS = ("quiz", "micro", "code")


def merged_values(rows):
    """Merged tier/cert values for a group of variant rows."""
    m = {}
    for t in TIERS:
        m[f"p_mastery_{t}"] = max((r[f"p_mastery_{t}"] or 0.0) for r in rows)
        m[f"n_evidence_{t}"] = sum((r[f"n_evidence_{t}"] or 0) for r in rows)
        lasts = [r[f"last_{t}_at"] for r in rows if r[f"last_{t}_at"]]
        m[f"last_{t}_at"] = max(lasts) if lasts else None
        lams = [r[f"decay_{t}_lam"] for r in rows if r[f"decay_{t}_lam"] is not None]
        m[f"decay_{t}_lam"] = min(lams) if lams else None
        m[f"hs_{t}"] = max((r[f"hs_{t}"] or 0.0) for r in rows)
    m["ease_factor"] = max((r["ease_factor"] or 2.5) for r in rows)
    m["is_certified"] = max((r["is_certified"] or 0) for r in rows)
    m["ever_certified"] = max((r["ever_certified"] or 0) for r in rows)
    m["p_mastery"] = max((r["p_mastery"] or 0.0) for r in rows)
    return m


def total_evidence(r):
    return sum((r[f"n_evidence_{t}"] or 0) for t in TIERS)


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT rowid, * FROM user_knowledge").fetchall()
    groups = defaultdict(list)
    for r in rows:
        if is_canonical_topic(r["concept"]):
            groups[(r["username"], canonical_concept(r["concept"]))].append(r)

    plan = []
    for (user, canon), grp in groups.items():
        names = {r["concept"] for r in grp}
        if names != {canon} or len(grp) > 1:
            plan.append((user, canon, grp, names))

    print(f"{len(plan)} (user, topic) group(s) to merge:\n")
    for user, canon, grp, names in plan[:60]:
        print(f"  {user}  {sorted(names)} → {canon}   ({len(grp)} rows)")
    if len(plan) > 60:
        print(f"  … and {len(plan) - 60} more")

    if not APPLY:
        print("\nDRY RUN — nothing written. Re-run with --apply to migrate (a timestamped "
              "backup of the DB is made first).")
        return

    bak = DB + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(DB, bak)
    print(f"\nBacked up DB → {bak}")

    migrated = 0
    with conn:
        for user, canon, grp, names in plan:
            m = merged_values(grp)
            # Survivor: prefer an already-canonical row, else the most-evidenced one.
            survivor = next((r for r in grp if r["concept"] == canon), None) \
                or max(grp, key=total_evidence)
            for r in grp:
                if r["rowid"] != survivor["rowid"]:
                    conn.execute("DELETE FROM user_knowledge WHERE rowid=?", (r["rowid"],))
            sets = ", ".join(f"{k}=?" for k in m) + ", concept=?"
            conn.execute(
                f"UPDATE user_knowledge SET {sets} WHERE rowid=?",
                [*m.values(), canon, survivor["rowid"]],
            )
            migrated += 1
    print(f"Merged {migrated} group(s). Fine nodes and unknown concepts left untouched.")


if __name__ == "__main__":
    main()
