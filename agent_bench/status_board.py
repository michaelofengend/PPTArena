#!/usr/bin/env python3
"""Live leaderboard board for the agent_bench cohort.

Serves a self-refreshing HTML page: one row per system, ranked by PPTArena
score, showing generation progress and both judges (Kimi K2.6 + Qwen3.7)
side by side, with live "judging N/25" progress. Reads the same CSVs the
deployed leaderboard consumes (agent_bench/results/*_judge_results.csv and
*_qwen_judge_results.csv) plus predictions on disk and the run manifest.

Usage (on the VM, from the repo root):
    python3 agent_bench/status_board.py --host 0.0.0.0 --port 80
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BENCH = Path(__file__).resolve().parent
MANIFEST = BENCH / "predictions" / "manifest.csv"
PREDICTIONS = BENCH / "predictions"
RESULTS = BENCH / "results"
# Must match run_agents.WORKDIRS_ROOT (workdirs live outside the repo tree).
WORKDIRS = Path(os.environ.get("AGENT_BENCH_WORKDIRS")
                or Path.home() / "agent_bench_workdirs")
SUBSET_PATH = BENCH / "subset25.json"
N_CASES = 25

# Canonical cohort: (id, display, kind, accent). Order is a fallback before
# scores exist; the board re-sorts by score once judged.
SYSTEMS = [
    ("codex_gpt55",          "Codex (GPT-5.5 xhigh)",    "CLI agent",  "#1a7f64"),
    ("claude_code_opus48",   "Claude Code (Opus 4.8)",   "CLI agent",  "#d97757"),
    ("gemini_cli_35flash",   "Gemini CLI (3.5 Flash)",   "CLI agent",  "#4285f4"),
    ("opencode_glm52",       "OpenCode (GLM-5.2)",       "CLI agent",  "#2563eb"),
    ("opencode_minimax_m3",  "OpenCode (MiniMax-M3)",    "CLI agent",  "#6d5dfc"),
    ("opencode_deepseek_v4", "OpenCode (DeepSeek V4)",   "CLI agent",  "#7c3aed"),
    ("opencode_kimi_k27code","OpenCode (Kimi K2.7)",     "CLI agent",  "#16181d"),
    ("cua_claude37",         "Claude 3.7 Sonnet",        "CUA",        "#d97757"),
    ("cua_chatgpt_agent",    "ChatGPT Agent",            "CUA",        "#0f0f0f"),
    ("cua_minimax_agent",    "MiniMax Agent",            "CUA",        "#6d5dfc"),
    ("pptpilot_kimi_k26",    "PPTPilot (Kimi K2.6)",     "our system", "#2563eb"),
    ("pptpilot_gemma431",    "PPTPilot (Gemma 4 31B)",   "our system", "#7c3aed"),
]
KIND_COLOR = {"CLI agent": "#22d3ee", "CUA": "#f59e0b", "our system": "#a5b4fc"}


def _read_scores(path: Path):
    if not path.exists():
        return None
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None
    ifs = [float(r.get("instruction_following_score") or 0) for r in rows]
    vqs = [float(r.get("visual_quality_score") or 0) for r in rows]
    return {"n": len(rows), "if": sum(ifs) / len(ifs), "vq": sum(vqs) / len(vqs)}


def load_state():
    systems = []
    for sid, name, kind, accent in SYSTEMS:
        gen = len(list((PREDICTIONS / sid).glob("*.pptx"))) if (PREDICTIONS / sid).is_dir() else 0
        kimi = _read_scores(RESULTS / f"{sid}_judge_results.csv")
        qwen = _read_scores(RESULTS / f"{sid}_qwen_judge_results.csv")
        score = (kimi["if"] + kimi["vq"]) * 10 if kimi else None  # 0-5 scale -> %
        systems.append({"id": sid, "name": name, "kind": kind, "accent": accent,
                        "gen": gen, "kimi": kimi, "qwen": qwen, "score": score})
    # Rank: judged systems by score desc, then unjudged by generation, stable.
    systems.sort(key=lambda s: (s["score"] is None, -(s["score"] or 0), -s["gen"]))

    recent = []
    if MANIFEST.exists():
        with MANIFEST.open() as fh:
            rows = list(csv.DictReader(fh))
        recent = sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)[:14]
    return systems, recent


def _cell(sc):
    if not sc:
        return '<td class="dim">—</td><td class="dim">—</td>'
    tag = "" if sc["n"] >= N_CASES else f'<span class="prog">{sc["n"]}/{N_CASES}</span>'
    return (f'<td>{sc["if"]:.2f}<span class="s">/{sc["vq"]:.2f}</span></td>'
            f'<td class="score">{(sc["if"]+sc["vq"])*10:.1f}%{tag}</td>')


def render() -> str:
    systems, recent = load_state()
    n_judged = sum(1 for s in systems if s["kimi"] and s["kimi"]["n"] >= N_CASES)
    n_gen = sum(s["gen"] for s in systems)
    judging = any(s["kimi"] and 0 < s["kimi"]["n"] < N_CASES for s in systems) or \
              any(s["qwen"] and 0 < s["qwen"]["n"] < N_CASES for s in systems)

    body = ""
    for i, s in enumerate(systems, 1):
        kc = KIND_COLOR[s["kind"]]
        genbar = f'{s["gen"]}/{N_CASES}' if s["gen"] else '—'
        body += (
            f'<tr>'
            f'<td class="rk">{i}</td>'
            f'<td><span class="dot" style="background:{s["accent"]}"></span>'
            f'<b>{html.escape(s["name"])}</b> '
            f'<span class="kind" style="color:{kc};border-color:{kc}44">{s["kind"]}</span></td>'
            f'<td class="gen">{genbar}</td>'
            f'{_cell(s["kimi"])}'
            f'{_cell(s["qwen"])}'
            f'</tr>')

    trs = ""
    for r in recent:
        c = {"ok": "#22c55e", "timeout": "#ef4444", "no_change": "#eab308"}.get(r["status"], "#94a3b8")
        trs += (f'<tr><td>{html.escape(r.get("timestamp","")[11:19])}</td>'
                f'<td>{html.escape(r["agent"])}</td><td>{html.escape(r["slug"])}</td>'
                f'<td style="color:{c};font-weight:600">{html.escape(r["status"])}</td></tr>')

    live = ('<span class="badge live">● judging live</span>' if judging
            else '<span class="badge done">✓ idle</span>')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>PPTArena — live cohort board</title>
<style>
 body{{font-family:ui-sans-serif,system-ui;background:#0b1220;color:#e2e8f0;margin:0;padding:26px 30px}}
 h1{{font-size:20px;margin:0 0 3px}} .sub{{color:#64748b;font-size:12.5px;margin-bottom:16px}}
 .badge{{font-size:11px;font-weight:600;padding:2px 9px;border-radius:20px;margin-left:8px;vertical-align:middle}}
 .live{{background:#3f1d1d;color:#fca5a5}} .live::before{{content:"";}} .done{{background:#14321f;color:#86efac}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 thead td{{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.06em;padding:6px 10px;border-bottom:1px solid #1e293b}}
 tbody td{{padding:9px 10px;border-bottom:1px solid #131c2e;white-space:nowrap}}
 .rk{{color:#475569;width:26px}} .dot{{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:8px;vertical-align:middle}}
 .kind{{font-size:10px;border:1px solid;border-radius:5px;padding:1px 6px;margin-left:6px}}
 .gen{{color:#94a3b8;font-variant-numeric:tabular-nums}}
 .score{{font-weight:700;font-variant-numeric:tabular-nums}} .s{{color:#64748b}}
 .prog{{color:#fca5a5;font-size:10px;margin-left:5px}} .dim{{color:#334155}}
 .grp{{color:#475569;font-size:11px;padding:10px 10px 4px}}
 .lo{{margin-top:22px}} .lo td{{font-size:12px;color:#94a3b8;padding:5px 10px;border-bottom:1px solid #131c2e}}
</style></head><body>
<h1>PPTArena — agent cohort, live {live}</h1>
<div class="sub">auto-refreshes 10s · {time.strftime('%H:%M:%S UTC')} · {n_judged}/{len(systems)} systems fully judged ·
  {n_gen} predictions generated · ranked by PPTArena score (mean (IF+VQ)/10)</div>
<table>
<thead><tr><td></td><td>system</td><td>gen</td>
  <td colspan="2" style="color:#a5b4fc">Kimi K2.6 &nbsp;IF/VQ · score</td>
  <td colspan="2" style="color:#fbbf24">Qwen3.7 &nbsp;IF/VQ · score</td></tr></thead>
<tbody>{body}</tbody></table>
<div class="lo"><table><thead><tr><td>recent generation events</td></tr></thead>
<tbody>{trs}</tbody></table></div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8377)
    args = ap.parse_args()
    print(f"serving on http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
