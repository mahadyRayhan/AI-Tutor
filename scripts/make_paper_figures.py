import pandas as pd
import matplotlib.pyplot as plt

def save_security_audit_plot(sec_csv="security_audit.csv", out="fig_security_audit_by_category.png"):
    df = pd.read_csv(sec_csv)
    df["is_safe"] = (df["verdict"].str.upper() == "SAFE").astype(int)
    g = df.groupby("cat").agg(n=("id","count"), safe=("is_safe","sum"))
    g["fail"] = g["n"] - g["safe"]

    # stacked bars: safe + fail
    ax = g[["safe","fail"]].plot(kind="bar", stacked=True, figsize=(10,4))
    ax.set_ylabel("Count")
    ax.set_title("Security Audit Outcomes by Category (SAFE vs FAIL)")
    plt.xticks(rotation=35, ha="right")
    plt.tight_layout()
    plt.savefig(out, dpi=300)
    plt.close()

def save_perf_pathway_plot(perf_csv="benchmark_engineering_performance.csv", out="fig_latency_by_pathway.png"):
    df = pd.read_csv(perf_csv).copy()
    df = df.rename(columns={
        "Category":"Pathway",
        "Security_ms":"Security_ms",
        "Retrieval_ms":"Retrieval_ms",
        "AI_s":"Gen_s"
    })
    # convert seconds to ms for consistent stacking
    df["Gen_ms"] = df["Gen_s"] * 1000.0
    plot_df = df.set_index("Pathway")[["Security_ms","Retrieval_ms","Gen_ms"]]

    ax = plot_df.plot(kind="bar", stacked=True, figsize=(10,4))
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Latency Breakdown by Interaction Pathway")
    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()
    plt.savefig(out, dpi=300)
    plt.close()

def save_judge_raw_vs_adjusted(
    raw_csv="benchmark_llm_judge_quality.csv",
    adj_csv="benchmark_recalibrate_llm_judge.csv",
    out="fig_judge_raw_vs_adjusted.png"
):
    raw = pd.read_csv(raw_csv)
    adj = pd.read_csv(adj_csv)

    raw_mean = raw[["correctness_score","persona_score","retrieval_score"]].mean()
    adj_mean = adj[["correctness_score","persona_score","retrieval_score"]].mean()

    df = pd.DataFrame({"Raw": raw_mean, "Adjusted": adj_mean})
    ax = df.plot(kind="bar", figsize=(8,4))
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Score")
    ax.set_title("LLM-as-a-Judge: Raw vs Recalibrated Scores")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(out, dpi=300)
    plt.close()

if __name__ == "__main__":
    save_security_audit_plot()
    save_perf_pathway_plot()
    save_judge_raw_vs_adjusted()
    print("Done. Saved: fig_security_audit_by_category.png, fig_latency_by_pathway.png, fig_judge_raw_vs_adjusted.png")
