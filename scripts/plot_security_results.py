import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Data from your audit results
data = {
    "Category": [
        "RBAC / Leakage", "Social Eng.", "Jailbreak (DAN)", "Indirect Poison",
        "Infrastructure", "Privacy", "Output Safety", "Tool Abuse", "RAG Integrity"
    ],
    "Total Tests": [5, 5, 5, 3, 4, 4, 4, 4, 2],
    "Passed":      [5, 5, 4, 3, 4, 4, 3, 4, 2]
}

df = pd.DataFrame(data)
df["Success Rate"] = (df["Passed"] / df["Total Tests"]) * 100
df["Failure Rate"] = 100 - df["Success Rate"]

# Setup Style
plt.figure(figsize=(10, 6))
sns.set_theme(style="whitegrid")

# Create Stacked Bar Chart
bar1 = plt.bar(df["Category"], df["Success Rate"], color='#2ecc71', label='Blocked / Safe')
bar2 = plt.bar(df["Category"], df["Failure Rate"], bottom=df["Success Rate"], color='#e74c3c', label='Behavioral Failure')

# Add Labels
plt.ylabel('Defense Success Rate (%)', fontsize=12)
plt.title('Security Audit Results by Attack Vector (N=36)', fontsize=14, weight='bold')
plt.xticks(rotation=45, ha="right")
plt.legend(loc='lower right')
plt.ylim(0, 110)

# Add text annotations
for i, rect in enumerate(bar1):
    height = rect.get_height()
    plt.text(rect.get_x() + rect.get_width()/2.0, 50, f"{df['Passed'][i]}/{df['Total Tests'][i]}", ha='center', va='bottom', color='white', weight='bold')

plt.tight_layout()
plt.savefig("security_audit_chart.png", dpi=300)
print("Chart saved as security_audit_chart.png")