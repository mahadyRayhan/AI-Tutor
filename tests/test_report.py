# tests/test_report.py
"""
HTML Report Generator for AI Tutor Test Results.
Produces a visual report with per-turn scores, scenario summaries, and flagged issues.
"""

import json
from datetime import datetime
from typing import List
from tests.test_evaluator import ScenarioResult, TurnScore


def _score_color(score: float) -> str:
    """Returns a CSS color based on the score (0-1 scale)."""
    if score >= 0.85: return "#34d399"   # green
    elif score >= 0.70: return "#60a5fa"  # blue
    elif score >= 0.55: return "#fbbf24"  # yellow
    elif score >= 0.40: return "#fb923c"  # orange
    else: return "#f87171"                # red


def _grade_badge(grade: str) -> str:
    colors = {"A": "#34d399", "B": "#60a5fa", "C": "#fbbf24", "D": "#fb923c", "F": "#f87171"}
    c = colors.get(grade, "#888")
    return f'<span style="background:{c};color:#000;padding:2px 10px;border-radius:12px;font-weight:700;font-size:0.9rem">{grade}</span>'


def _render_turn_row(turn: TurnScore) -> str:
    """Render a single turn as a table row."""
    if turn.is_reactive:
        return f"""
        <tr style="opacity:0.6">
            <td>{turn.turn_index}</td>
            <td><em style="color:#888">🤖 Auto: {turn.user_message[:60]}...</em></td>
            <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
        </tr>"""
    
    overall = turn.overall_score
    color = _score_color(overall)
    
    return f"""
    <tr>
        <td>{turn.turn_index}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{turn.user_message}">{turn.user_message[:60]}</td>
        <td style="color:{_score_color(turn.cosine_similarity)}">{turn.cosine_similarity:.2f}</td>
        <td>{turn.accuracy}/5</td>
        <td>{turn.completeness}/5</td>
        <td>{turn.pedagogy}/5</td>
        <td>{turn.relevance}/5</td>
        <td style="color:{color};font-weight:700">{overall:.2f}</td>
        <td>{_grade_badge(turn.grade)}</td>
    </tr>"""


def _render_scenario_card(result: ScenarioResult) -> str:
    """Render a single scenario as an expandable card."""
    status = "✅ PASS" if result.passed else "❌ FAIL"
    status_color = "#34d399" if result.passed else "#f87171"
    
    turn_rows = "\n".join(_render_turn_row(t) for t in result.turns)
    error_html = ""
    if result.errors:
        error_html = '<div style="background:#451a1a;border:1px solid #f87171;padding:10px;border-radius:8px;margin:10px 0">'
        error_html += "<strong>⚠️ Errors:</strong><ul>"
        for e in result.errors:
            error_html += f"<li>{e}</li>"
        error_html += "</ul></div>"
    
    consistency_html = ""
    if result.consistency_score > 0:
        c_color = _score_color(result.consistency_score)
        consistency_html = f'<span style="margin-left:15px;color:{c_color}">Consistency: {result.consistency_score:.2f}</span>'
    
    return f"""
    <details style="background:#1e1f20;border:1px solid #333;border-radius:12px;padding:15px;margin:12px 0">
        <summary style="cursor:pointer;font-size:1.1rem;font-weight:600;color:#e3e3e3">
            <span style="color:{status_color}">{status}</span> — {result.scenario_name}
            <span style="float:right;color:{_score_color(result.avg_overall)};font-weight:700">
                Overall: {result.avg_overall:.2f}
            </span>
            {consistency_html}
        </summary>
        
        {error_html}
        
        <div style="overflow-x:auto;margin-top:12px">
            <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
                <thead>
                    <tr style="border-bottom:2px solid #444;text-align:left">
                        <th style="padding:8px">#</th>
                        <th style="padding:8px">Message</th>
                        <th style="padding:8px">Cosine</th>
                        <th style="padding:8px">Acc</th>
                        <th style="padding:8px">Comp</th>
                        <th style="padding:8px">Ped</th>
                        <th style="padding:8px">Rel</th>
                        <th style="padding:8px">Overall</th>
                        <th style="padding:8px">Grade</th>
                    </tr>
                </thead>
                <tbody>
                    {turn_rows}
                </tbody>
            </table>
        </div>
        
        <details style="margin-top:10px">
            <summary style="cursor:pointer;color:#888;font-size:0.85rem">📜 Full Transcript</summary>
            <div style="background:#111;padding:12px;border-radius:8px;margin-top:8px;font-size:0.8rem;max-height:400px;overflow-y:auto">
                {"".join(f'<div style="margin:8px 0"><strong style="color:#60a5fa">Student:</strong> {t.user_message}<br><strong style="color:#34d399">Tutor:</strong> {t.bot_response[:300]}{"..." if len(t.bot_response) > 300 else ""}<br><em style="color:#888">Judge: {t.judge_reasoning}</em></div><hr style="border-color:#333">' for t in result.turns)}
            </div>
        </details>
    </details>"""


def generate_html_report(
    results: List[ScenarioResult],
    output_path: str,
    run_duration_s: float = 0.0
) -> str:
    """Generate a complete HTML report and save to file."""
    
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    
    # Aggregate metrics
    all_cosines = [t.cosine_similarity for r in results for t in r.turns if not t.is_reactive and t.cosine_similarity > 0]
    all_judges = [t.llm_judge_avg for r in results for t in r.turns if not t.is_reactive and t.llm_judge_avg > 0]
    
    avg_cosine = sum(all_cosines) / len(all_cosines) if all_cosines else 0
    avg_judge = sum(all_judges) / len(all_judges) if all_judges else 0
    
    scenario_cards = "\n".join(_render_scenario_card(r) for r in results)
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Tutor Test Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', -apple-system, sans-serif; background: #0a0a0a; color: #e3e3e3; padding: 20px; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        h1 {{ font-size: 1.8rem; margin-bottom: 5px; }}
        .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 20px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 20px 0; }}
        .card {{ background: #1e1f20; border: 1px solid #333; border-radius: 12px; padding: 18px; text-align: center; }}
        .card .value {{ font-size: 2rem; font-weight: 700; }}
        .card .label {{ color: #888; font-size: 0.8rem; margin-top: 4px; }}
        table td, table th {{ padding: 8px; }}
        table tbody tr {{ border-bottom: 1px solid #222; }}
        table tbody tr:hover {{ background: #262626; }}
        hr {{ border: none; border-top: 1px solid #333; margin: 20px 0; }}
    </style>
</head>
<body>
<div class="container">
    <h1>🧪 AI Tutor — Autonomous Test Report</h1>
    <div class="meta">Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')} · Duration: {run_duration_s:.1f}s</div>
    
    <div class="grid">
        <div class="card">
            <div class="value" style="color:{'#34d399' if passed == total else '#fbbf24'}">{passed}/{total}</div>
            <div class="label">Scenarios Passed</div>
        </div>
        <div class="card">
            <div class="value" style="color:{_score_color(avg_cosine)}">{avg_cosine:.2f}</div>
            <div class="label">Avg Cosine Grounding</div>
        </div>
        <div class="card">
            <div class="value" style="color:{_score_color(avg_judge / 5)}">{avg_judge:.1f}/5</div>
            <div class="label">Avg LLM Judge Score</div>
        </div>
        <div class="card">
            <div class="value" style="color:#f87171">{failed}</div>
            <div class="label">Failed Scenarios</div>
        </div>
    </div>
    
    <hr>
    <h2 style="margin-bottom:10px">📋 Scenario Results</h2>
    {scenario_cards}
    
    <hr>
    <div style="text-align:center;color:#555;font-size:0.75rem;margin-top:20px">
        AI Tutor Autonomous Testing Agent · Context-Aware Evaluation
    </div>
</div>
</body>
</html>"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return output_path


def generate_json_report(results: List[ScenarioResult], output_path: str) -> str:
    """Generate a machine-readable JSON report."""
    data = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_scenarios": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
        },
        "scenarios": []
    }
    
    for r in results:
        scenario = {
            "name": r.scenario_name,
            "passed": bool(r.passed),
            "avg_cosine": round(float(r.avg_cosine), 3),
            "avg_judge": round(float(r.avg_judge), 3),
            "avg_overall": round(float(r.avg_overall), 3),
            "consistency": round(r.consistency_score, 3) if r.consistency_score > 0 else None,
            "errors": r.errors,
            "turns": [
                {
                    "index": t.turn_index,
                    "message": t.user_message,
                    "response_preview": t.bot_response[:200],
                    "cosine": round(t.cosine_similarity, 3),
                    "accuracy": t.accuracy,
                    "completeness": t.completeness,
                    "pedagogy": t.pedagogy,
                    "relevance": t.relevance,
                    "overall": round(t.overall_score, 3),
                    "grade": t.grade,
                    "is_reactive": t.is_reactive
                }
                for t in r.turns
            ]
        }
        data["scenarios"].append(scenario)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    
    return output_path
