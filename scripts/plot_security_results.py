import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import os

def main():
    base_dir = os.path.join(os.path.dirname(__file__), "..", "eval_result")
    csv_path = os.path.join(base_dir, "security_audit.csv")
    img_path = os.path.join(base_dir, "security_audit_chart.png")

    if not os.path.exists(csv_path):
        print("CSV not found. Run security_audit.py first.")
        return

    # Load Data
    df_raw = pd.read_csv(csv_path)
    
    # Map Verdicts to Binary
    # SAFE / PASS = 1, FAIL / LEAK = 0
    df_raw['is_safe'] = df_raw['verdict'].apply(lambda x: 1 if "SAFE" in x or "PASS" in x else 0)
    
    # Group by Category
    summary = df_raw.groupby('cat').agg(
        total=('id', 'count'),
        passed=('is_safe', 'sum')
    ).reset_index()

    summary["Success Rate"] = (summary["passed"] / summary["total"]) * 100
    summary["Failure Rate"] = 100 - summary["Success Rate"]

    # Plot
    plt.figure(figsize=(12, 6))
    sns.set_theme(style="whitegrid")

    # Stacked Bar
    bar1 = plt.bar(summary["cat"], summary["Success Rate"], color='#2ecc71', label='Blocked / Safe')
    bar2 = plt.bar(summary["cat"], summary["Failure Rate"], bottom=summary["Success Rate"], color='#e74c3c', label='Behavioral Failure')

    plt.ylabel('Defense Success Rate (%)', fontsize=12)
    plt.title(f'Security Audit Results (N={len(df_raw)})', fontsize=14, weight='bold')
    plt.xticks(rotation=45, ha="right")
    plt.legend(loc='lower right')
    plt.ylim(0, 110)

    # Annotations
    for i, rect in enumerate(bar1):
        total = summary['total'][i]
        passed = summary['passed'][i]
        plt.text(rect.get_x() + rect.get_width()/2.0, 50, f"{passed}/{total}", ha='center', va='bottom', color='white', weight='bold')

    plt.tight_layout()
    plt.savefig(img_path, dpi=300)
    print(f"Chart saved to {img_path}")

if __name__ == "__main__":
    main()