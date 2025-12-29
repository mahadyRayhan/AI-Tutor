#!/bin/bash

echo "🚀 STARTING FULL SYSTEM EVALUATION 🚀"
echo "========================================"

# 0. Setup Environment
# Ensure output directory exists
mkdir -p eval_result

# 1. DATA INGESTION (Crucial for Security Traps)
echo "\n[1/6] Ingesting Data & Planting Security Traps..."
python scripts/ingest_data.py
python scripts/ingest_manual_graph.py

# 2. BASELINE DATA GENERATION (Slowest Step - API Calls)
echo "\n[2/6] Generating Baseline Comparison Data (N=80)..."
# This creates benchmark_base_comp_datagen.json
python scripts/benchmark_base_comp_datagen.py

# 3. ENGINEERING PERFORMANCE (Latency)
echo "\n[3/6] Running Latency Benchmarks..."
# This creates benchmark_engineering_performance.csv
python scripts/benchmark_engineering_performance.py
# This creates benchmark_detailed_latency.csv
python scripts/benchmark_latency.py

# 4. QUALITY EVALUATION (LLM Judge)
echo "\n[4/6] Running Quality & Security Audits..."
# This judges the Quality (Pedagogy/Groundedness) -> benchmark_judge_full.csv
python scripts/benchmark_llm_judge_quality.py
# This audits Security (RBAC/Injection) -> security_audit.csv
python scripts/security_audit.py
# This compares GPT/Gemini/Ours -> final_rigorous_results.csv
python scripts/evaluate_benchmark_data.py

# 5. POST-PROCESSING & ANALYSIS
echo "\n[5/6] Calculating Final Scores..."
# Recalibrates the Judge scores -> benchmark_recalibrate_llm_judge.csv
python scripts/benchmark_recalibrate_llm_judge.py

# 6. VISUALIZATION
echo "\n[6/6] Generating Plots..."
# Creates security_audit_chart.png
python scripts/plot_security_results.py

echo "\n========================================"
echo "✅ EVALUATION COMPLETE!"
echo "📂 All results are saved in: /eval_result"
echo "========================================"