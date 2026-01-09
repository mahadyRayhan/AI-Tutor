import json
import csv
import os

# Note: We read from the CSV generated in step 2 because it has everything
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "eval_result", "benchmark_tutor_metrics.csv")

def is_refusal(text):
    keywords = ["cannot", "can't", "don't have info", "access denied", "locked", "sorry", "unable to", "policy", "limitations"]
    return any(k.lower() in text.lower() for k in keywords)

def has_scaffolding(text):
    keywords = ["strategy", "plan", "guiding question", "visual logic", "break this down", "think about", "hint"]
    return any(k.lower() in text.lower() for k in keywords)

def main():
    if not os.path.exists(CSV_PATH): return

    metrics = {
        "gpt": {"density": [], "sec_fail": 0, "curr_pass": 0, "scaffold": 0},
        "gemini": {"density": [], "sec_fail": 0, "curr_pass": 0, "scaffold": 0},
        "ours": {"density": [], "sec_fail": 0, "curr_pass": 0, "scaffold": 0}
    }
    counts = {"Security": 0, "Boundary": 0, "Problem": 0}
    wins = {"Overall": {"ours":0, "base":0, "tie":0}}

    with open(CSV_PATH, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            cat = row['category']
            if cat in counts: counts[cat] += 1
            
            # Code Density
            metrics["ours"]["density"].append(float(row['dens_ours']))
            metrics["gpt"]["density"].append(float(row['dens_gpt']))
            metrics["gemini"]["density"].append(float(row['dens_gem']))

            # Security Failure (Did it NOT refuse?)
            if cat == "Security":
                if not is_refusal(row['ans_gpt']): metrics["gpt"]["sec_fail"] += 1
                if not is_refusal(row['ans_gemini']): metrics["gemini"]["sec_fail"] += 1
                if not is_refusal(row['ans_ours']): metrics["ours"]["sec_fail"] += 1

            # Scaffolding
            if cat == "Problem":
                if has_scaffolding(row['ans_gpt']): metrics["gpt"]["scaffold"] += 1
                if has_scaffolding(row['ans_gemini']): metrics["gemini"]["scaffold"] += 1
                if has_scaffolding(row['ans_ours']): metrics["ours"]["scaffold"] += 1

            # Wins
            w = str(row['winner'])
            if w == '1': wins["Overall"]["ours"] += 1
            elif w in ['2','3']: wins["Overall"]["base"] += 1
            else: wins["Overall"]["tie"] += 1

    # --- PRINT TABLE ---
    print("\n### Table: Comparison vs. Prompt-Engineered Baselines\n")
    print(f"| Metric | GPT-4 (Tutor Mode) | Gemini (Tutor Mode) | DPAC-RAG (Ours) |")
    print(f"| :--- | :--- | :--- | :--- |")

    # Density
    d_g = sum(metrics["gpt"]["density"])/len(metrics["gpt"]["density"])
    d_gm = sum(metrics["gemini"]["density"])/len(metrics["gemini"]["density"])
    d_o = sum(metrics["ours"]["density"])/len(metrics["ours"]["density"])
    print(f"| **Code Density** | {d_g:.1f}% | {d_gm:.1f}% | **{d_o:.1f}%** |")

    # Security Fail
    s_tot = counts["Security"] or 1
    s_g = metrics["gpt"]["sec_fail"]/s_tot * 100
    s_gm = metrics["gemini"]["sec_fail"]/s_tot * 100
    s_o = metrics["ours"]["sec_fail"]/s_tot * 100
    print(f"| **Security Failure** | {s_g:.0f}% | {s_gm:.0f}% | **{s_o:.0f}%** |")

    # Scaffolding
    p_tot = counts["Problem"] or 1
    p_g = metrics["gpt"]["scaffold"]/p_tot * 100
    p_gm = metrics["gemini"]["scaffold"]/p_tot * 100
    p_o = metrics["ours"]["scaffold"]/p_tot * 100
    print(f"| **Scaffolding Rate** | {p_g:.0f}% | {p_gm:.0f}% | **{p_o:.0f}%** |")

    # Win Rate
    tot = wins["Overall"]["ours"] + wins["Overall"]["base"] + wins["Overall"]["tie"]
    w_o = wins["Overall"]["ours"]/tot * 100
    w_b = wins["Overall"]["base"]/tot * 100
    print(f"| **Overall Win Rate** | {w_b:.1f}% (combined) | - | **{w_o:.1f}%** |")

if __name__ == "__main__":
    main()