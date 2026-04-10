# tests/test_report.py
"""
HTML & JSON Report Generator for AI Tutor Test Results.
Produces a visual report with per-turn scores, scenario summaries,
advanced metrics (SFI, PVR, SRR, SMD, ETI, KTE), and flagged issues.
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


def _metric_color(value: float, invert: bool = False) -> str:
    """Color for advanced metrics. invert=True means lower is better (PVR, SFI)."""
    if invert:
        if value <= 20: return "#34d399"
        elif value <= 40: return "#60a5fa"
        elif value <= 60: return "#fbbf24"
        elif value <= 80: return "#fb923c"
        else: return "#f87171"
    else:
        if value >= 0.85: return "#34d399"
        elif value >= 0.70: return "#60a5fa"
        elif value >= 0.55: return "#fbbf24"
        elif value >= 0.40: return "#fb923c"
        else: return "#f87171"


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
            <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
        </tr>"""
    
    overall = turn.overall_score
    color = _score_color(overall)
    eti = turn.eti_score
    eti_color = _score_color(eti) if eti > 0 else "#555"
    
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
        <td style="color:{eti_color}">{eti:.2f}</td>
    </tr>"""


def _render_advanced_metrics(result: ScenarioResult) -> str:
    """Render the advanced metrics bar for a scenario."""
    metrics = []
    
    # SFI (lower is better — inverted color)
    sfi_color = _metric_color(result.sfi, invert=True)
    metrics.append(f'<span style="color:{sfi_color}">SFI: {result.sfi:.0f}%</span>')
    
    # PVR (lower is better — inverted color)
    pvr_color = _metric_color(result.pvr, invert=True)
    metrics.append(f'<span style="color:{pvr_color}">PVR: {result.pvr:.0f}%</span>')
    
    # ETI (higher is better)
    eti_color = _score_color(result.eti) if result.eti > 0 else "#555"
    metrics.append(f'<span style="color:{eti_color}">ETI: {result.eti:.2f}</span>')
    
    # SMD (positive is better)
    if result.smd is not None:
        smd_color = "#34d399" if result.smd > 0 else "#f87171" if result.smd < 0 else "#888"
        metrics.append(f'<span style="color:{smd_color}">SMD: {result.smd:+.4f}</span>')
    
    # KTE (> 1.0 is better)
    if result.kte is not None:
        kte_color = "#34d399" if result.kte >= 1.0 else "#fbbf24" if result.kte >= 0.5 else "#f87171"
        metrics.append(f'<span style="color:{kte_color}">KTE: {result.kte:.2f}</span>')
    
    # SRR (higher is better, Red Team only)
    if result.srr is not None:
        srr_color = "#34d399" if result.srr >= 90 else "#fbbf24" if result.srr >= 70 else "#f87171"
        metrics.append(f'<span style="color:{srr_color}">SRR: {result.srr:.0f}%</span>')
    
    return ' &nbsp;·&nbsp; '.join(metrics)


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
    
    advanced_html = _render_advanced_metrics(result)
    
    return f"""
    <details style="background:#1e1f20;border:1px solid #333;border-radius:12px;padding:15px;margin:12px 0">
        <summary style="cursor:pointer;font-size:1.1rem;font-weight:600;color:#e3e3e3">
            <span style="color:{status_color}">{status}</span> — {result.scenario_name}
            <span style="float:right;color:{_score_color(result.avg_overall)};font-weight:700">
                Overall: {result.avg_overall:.2f}
            </span>
            {consistency_html}
        </summary>
        
        <div style="margin:10px 0;padding:8px 12px;background:#262626;border-radius:8px;font-size:0.85rem">
            📊 <strong>Advanced Metrics:</strong> {advanced_html}
        </div>
        
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
                        <th style="padding:8px">ETI</th>
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
    
    # Aggregate core metrics
    all_cosines = [t.cosine_similarity for r in results for t in r.turns if not t.is_reactive and t.cosine_similarity > 0]
    all_judges = [t.llm_judge_avg for r in results for t in r.turns if not t.is_reactive and t.llm_judge_avg > 0]
    
    avg_cosine = sum(all_cosines) / len(all_cosines) if all_cosines else 0
    avg_judge = sum(all_judges) / len(all_judges) if all_judges else 0
    
    # Aggregate advanced metrics
    all_sfi = [r.sfi for r in results if r.scored_turns]
    all_pvr = [r.pvr for r in results if r.scored_turns]
    all_eti = [r.eti for r in results if r.eti > 0]
    all_smd = [r.smd for r in results if r.smd is not None]
    all_kte = [r.kte for r in results if r.kte is not None]
    all_srr = [r.srr for r in results if r.srr is not None]
    
    avg_sfi = sum(all_sfi) / len(all_sfi) if all_sfi else 0
    avg_pvr = sum(all_pvr) / len(all_pvr) if all_pvr else 0
    avg_eti = sum(all_eti) / len(all_eti) if all_eti else 0
    avg_smd = sum(all_smd) / len(all_smd) if all_smd else 0
    avg_kte = sum(all_kte) / len(all_kte) if all_kte else 0
    avg_srr = sum(all_srr) / len(all_srr) if all_srr else None
    
    scenario_cards = "\n".join(_render_scenario_card(r) for r in results)
    
    # Advanced metrics cards HTML
    adv_cards = f"""
        <div class="card">
            <div class="value" style="color:{_metric_color(avg_sfi, invert=True)}">{avg_sfi:.0f}%</div>
            <div class="label">Avg SFI<br><small style="color:#555">Spoon-Feeding Index</small></div>
        </div>
        <div class="card">
            <div class="value" style="color:{_metric_color(avg_pvr, invert=True)}">{avg_pvr:.0f}%</div>
            <div class="label">Avg PVR<br><small style="color:#555">Prereq Violation Rate</small></div>
        </div>
        <div class="card">
            <div class="value" style="color:{_score_color(avg_eti)}">{avg_eti:.2f}</div>
            <div class="label">Avg ETI<br><small style="color:#555">Emotional Tone Index</small></div>
        </div>
        <div class="card">
            <div class="value" style="color:{'#34d399' if avg_smd > 0 else '#f87171' if avg_smd < 0 else '#888'}">{avg_smd:+.3f}</div>
            <div class="label">Avg SMD<br><small style="color:#555">Semantic Maturity Δ</small></div>
        </div>
        <div class="card">
            <div class="value" style="color:{'#34d399' if avg_kte >= 1.0 else '#fbbf24' if avg_kte > 0 else '#555'}">{avg_kte:.2f}</div>
            <div class="label">Avg KTE<br><small style="color:#555">Knowledge Transfer Eff.</small></div>
        </div>"""
    
    if avg_srr is not None:
        adv_cards += f"""
        <div class="card">
            <div class="value" style="color:{'#34d399' if avg_srr >= 90 else '#fbbf24'}">{avg_srr:.0f}%</div>
            <div class="label">SRR<br><small style="color:#555">Security Recall Rate</small></div>
        </div>"""
    
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
        h2 {{ font-size: 1.3rem; }}
        .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 20px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0; }}
        .card {{ background: #1e1f20; border: 1px solid #333; border-radius: 12px; padding: 18px; text-align: center; }}
        .card .value {{ font-size: 2rem; font-weight: 700; }}
        .card .label {{ color: #888; font-size: 0.8rem; margin-top: 4px; }}
        table td, table th {{ padding: 8px; }}
        table tbody tr {{ border-bottom: 1px solid #222; }}
        table tbody tr:hover {{ background: #262626; }}
        hr {{ border: none; border-top: 1px solid #333; margin: 20px 0; }}
        small {{ font-size: 0.7rem; }}
    </style>
</head>
<body>
<div class="container">
    <h1>🧪 AI Tutor — Autonomous Test Report</h1>
    <div class="meta">Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')} · Duration: {run_duration_s:.1f}s</div>
    
    <h2>📐 Core Metrics</h2>
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
    
    <h2>📊 Advanced Metrics</h2>
    <div class="grid">
        {adv_cards}
    </div>
    
    <hr>
    <h2 style="margin-bottom:10px">📋 Scenario Results</h2>
    {scenario_cards}
    
    <hr>
    <div style="text-align:center;color:#555;font-size:0.75rem;margin-top:20px">
        AI Tutor Autonomous Testing Agent · Context-Aware Evaluation · Advanced Metrics v2
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
            # Core metrics
            "avg_cosine": round(float(r.avg_cosine), 3),
            "avg_judge": round(float(r.avg_judge), 3),
            "avg_overall": round(float(r.avg_overall), 3),
            "consistency": round(r.consistency_score, 3) if r.consistency_score > 0 else None,
            # Advanced metrics
            "sfi": round(r.sfi, 1),
            "pvr": round(r.pvr, 1),
            "eti": round(r.eti, 3),
            "smd": round(r.smd, 4) if r.smd is not None else None,
            "kte": round(r.kte, 3) if r.kte is not None else None,
            "srr": round(r.srr, 1) if r.srr is not None else None,
            # Errors
            "errors": r.errors,
            # Turns
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
                    "is_reactive": t.is_reactive,
                    # Advanced per-turn
                    "sfi_has_code": t.sfi_has_code_block,
                    "eti": round(t.eti_score, 3),
                    "eti_sub": {
                        "encouragement": round(t.eti_encouragement, 3),
                        "mirroring": round(t.eti_mirroring, 3),
                        "firmness_warmth": round(t.eti_firmness_warmth, 3),
                    },
                    "pvr_concept": t.pvr_concept_taught,
                    "pvr_violation": t.pvr_is_violation,
                }
                for t in r.turns
            ]
        }
        data["scenarios"].append(scenario)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    
    return output_path
