"""Plot security audit results from the CSV (single source of truth).

Reads:  eval_result/security_audit.csv
Writes: eval_result/security_audit_chart.png

The chart reports per-category "block rate" (SAFE / (SAFE+FAIL)) and excludes
INCONCLUSIVE rows from the denominator by default (so judge/API errors don't
inflate/deflate results unpredictably).
"""

import os
import pandas as pd
import matplotlib.pyplot as plt


def main() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    csv_path = os.path.join(root, "eval_result", "security_audit.csv")
    out_path = os.path.join(root, "eval_result", "security_audit_chart.png")

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Missing: {csv_path}. Run scripts/security_audit.py first.")

    df = pd.read_csv(csv_path)

    # Normalize verdict strings
    df["verdict"] = df["verdict"].astype(str).str.upper().str.strip()

    # Exclude inconclusive tests from the denominator (recommended for reporting)
    df_scored = df[df["verdict"].isin(["SAFE", "FAIL"])].copy()

    # Map CSV category names (code taxonomy) -> paper-friendly labels
    label_map = {
        "Data Leakage": "RBAC / Data Leakage",
        "Social Eng": "Social Engineering",
        "Indirect Inj": "Indirect Poisoning",
        "Jailbreak": "Jailbreak",
        "Infrastructure": "Infrastructure",
        "Privacy": "Privacy",
        "Tool Abuse": "Tool Abuse",
        "Output Safety": "Output Safety",
        "RAG Leak": "RAG Integrity",
    }

    df_scored["cat_label"] = df_scored["cat"].map(label_map).fillna(df_scored["cat"])

    # Order categories as shown in paper
    ordered_labels = [
        "RBAC / Data Leakage",
        "Social Engineering",
        "Jailbreak",
        "Indirect Poisoning",
        "Infrastructure",
        "Privacy",
        "Output Safety",
        "Tool Abuse",
        "RAG Integrity",
    ]

    # Aggregate
    agg = (
        df_scored.groupby("cat_label")["verdict"]
        .value_counts()
        .unstack(fill_value=0)
        .reindex(ordered_labels)
        .fillna(0)
    )
    if "SAFE" not in agg.columns:
        agg["SAFE"] = 0
    if "FAIL" not in agg.columns:
        agg["FAIL"] = 0

    total = (agg["SAFE"] + agg["FAIL"]).astype(int)
    safe = agg["SAFE"].astype(int)
    safe_rate = (safe / total.replace(0, 1)) * 100.0
    fail_rate = 100.0 - safe_rate

    # Plot (stacked bars)
    fig = plt.figure(figsize=(10, 5.8))
    ax = fig.add_subplot(111)

    x = range(len(ordered_labels))
    ax.bar(x, safe_rate.values, label="Blocked / Safe")
    ax.bar(x, fail_rate.values, bottom=safe_rate.values, label="Behavioral Failure")

    ax.set_ylabel("Defense Success Rate (%)")
    ax.set_title(f"Security Audit Results by Attack Vector (N={int(df.shape[0])})")
    ax.set_xticks(list(x))
    ax.set_xticklabels(ordered_labels, rotation=35, ha="right")
    ax.set_ylim(0, 110)
    ax.legend(loc="lower right")

    # Annotate counts (SAFE/TOTAL) in the middle of the SAFE segment
    for i, (s, t) in enumerate(zip(safe.values, total.values)):
        ax.text(i, 52, f"{s}/{t}", ha="center", va="center", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    print(f"✅ Chart saved: {out_path}")


if __name__ == "__main__":
    main()
