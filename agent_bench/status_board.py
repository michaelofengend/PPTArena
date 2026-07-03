#!/usr/bin/env python3
"""Live status board for agent_bench runs.

Serves a self-refreshing HTML page showing, per agent: finished/ok counts,
the case it is working on right now (inferred from workdir log activity),
and the latest completions from predictions/manifest.csv.

Usage (on the VM, from the repo root):
    python3 agent_bench/status_board.py                 # http://127.0.0.1:8377
    python3 agent_bench/status_board.py --host 0.0.0.0  # expose publicly

Then from your laptop:  ssh -L 8377:localhost:8377 root@<vm>  and open
http://localhost:8377
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BENCH = Path(__file__).resolve().parent
MANIFEST = BENCH / "predictions" / "manifest.csv"
WORKDIRS = BENCH / "workdirs"
AGENTS_PATH = BENCH / "agents.json"
SUBSET_PATH = BENCH / "subset25.json"

STATUS_COLORS = {
    "ok": "#22c55e", "no_change": "#eab308", "timeout": "#ef4444",
    "invalid_pptx": "#ef4444", "cli_missing": "#ef4444", "error": "#ef4444",
}
RUNNING_STALE_S = 25 * 60


def load_state():
    agents = json.loads(AGENTS_PATH.read_text())
    n_cases = len(json.loads(SUBSET_PATH.read_text()))
    rows = []
    if MANIFEST.exists():
        with MANIFEST.open() as fh:
            rows = list(csv.DictReader(fh))
    done = {}  # (agent, slug) -> row  (last row wins: reruns overwrite)
    for r in rows:
        done[(r["agent"], r["slug"])] = r

    running = []  # (agent, slug, elapsed_s, stale)
    now = time.time()
    if WORKDIRS.exists():
        for agent_dir in sorted(WORKDIRS.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name not in agents:
                continue
            for case_dir in sorted(agent_dir.iterdir()):
                if not case_dir.is_dir() or (agent_dir.name, case_dir.name) in done:
                    continue
                log = case_dir / "agent_output.log"
                anchor = log if log.exists() else case_dir
                mtime = anchor.stat().st_mtime
                started = case_dir.stat().st_mtime
                if now - mtime < RUNNING_STALE_S:
                    running.append((agent_dir.name, case_dir.name, now - started, False))
                else:
                    running.append((agent_dir.name, case_dir.name, now - started, True))
    judged = {}
    results_dir = BENCH / "results"
    if results_dir.exists():
        for f in sorted(results_dir.glob("*_judge_results.csv")):
            aid = f.name.replace("_judge_results.csv", "")
            with f.open() as fh:
                jrows = list(csv.DictReader(fh))
            if jrows:
                ifs = [float(r.get("instruction_following_score") or 0) for r in jrows]
                vqs = [float(r.get("visual_quality_score") or 0) for r in jrows]
                judged[aid] = {
                    "n": len(jrows),
                    "if_avg": sum(ifs) / len(ifs),
                    "vq_avg": sum(vqs) / len(vqs),
                    "cases": [(r.get("case_index", "?"),
                               float(r.get("instruction_following_score") or 0),
                               float(r.get("visual_quality_score") or 0)) for r in jrows],
                }
    return agents, n_cases, done, rows, running, judged


def fmt_dur(s):
    s = int(s)
    return f"{s//60}m{s%60:02d}s" if s >= 60 else f"{s}s"


def render() -> str:
    agents, n_cases, done, rows, running, judged = load_state()
    total = len(agents) * n_cases
    n_done = len(done)
    run_by_agent = {}
    for a, slug, el, stale in running:
        run_by_agent.setdefault(a, []).append((slug, el, stale))

    cards = []
    for aid, spec in agents.items():
        mine = [r for (a, _), r in done.items() if a == aid]
        ok = sum(1 for r in mine if r["status"] == "ok")
        bad = len(mine) - ok
        durs = [float(r["duration_seconds"]) for r in mine if r.get("duration_seconds")]
        avg = fmt_dur(sum(durs) / len(durs)) if durs else "—"
        now_running = run_by_agent.get(aid, [])
        run_html = "".join(
            f'<div class="run{" stale" if stale else ""}">{"⚠︎ stalled? " if stale else "▶ "}'
            f'{html.escape(slug)} · {fmt_dur(el)}</div>'
            for slug, el, stale in now_running) or '<div class="idle">idle</div>'
        j = judged.get(aid)
        if j:
            judge_html = (f'<div class="jline">judged {j["n"]}/{n_cases} · '
                          f'<b>IF {j["if_avg"]:.2f}</b> · <b>VQ {j["vq_avg"]:.2f}</b></div>')
        else:
            judge_html = '<div class="jline dim">not judged yet</div>'
        pct = int(100 * len(mine) / n_cases) if n_cases else 0
        cards.append(f"""
        <div class="card">
          <div class="name">{html.escape(spec.get('display_name', aid))}</div>
          <div class="bar"><i style="width:{pct}%"></i></div>
          <div class="nums"><b>{len(mine)}</b>/{n_cases} done · <span class="ok">{ok} ok</span>
               {f'· <span class="bad">{bad} issues</span>' if bad else ''} · avg {avg}</div>
          {run_html}
          {judge_html}
        </div>""")

    recent = sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)[:25]
    trs = []
    for r in recent:
        c = STATUS_COLORS.get(r["status"], "#94a3b8")
        trs.append(f"<tr><td>{html.escape(r.get('timestamp','')[11:19])}</td>"
                   f"<td>{html.escape(r['agent'])}</td><td>{html.escape(r['slug'])}</td>"
                   f"<td style='color:{c};font-weight:600'>{html.escape(r['status'])}</td>"
                   f"<td>{fmt_dur(float(r['duration_seconds'] or 0))}</td>"
                   f"<td class='notes'>{html.escape((r.get('notes') or '')[:70])}</td></tr>")

    if judged:
        all_cases = sorted({c for j in judged.values() for c, _, _ in j["cases"]}, key=lambda x: int(x) if str(x).isdigit() else 0)
        hdr = "".join(f"<td>c{c}</td>" for c in all_cases)
        body = ""
        for aid, j in judged.items():
            cell = {c: (i, v) for c, i, v in j["cases"]}
            tds = "".join(
                (lambda t: f"<td>{t[0]:.0f}/{t[1]:.0f}</td>" if t else "<td class=dim>·</td>")(cell.get(c))
                for c in all_cases)
            body += f"<tr><td><b>{html.escape(aid)}</b></td>{tds}</tr>"
        scores_table = (f'<h1 style="margin-top:22px">judged scores (IF/VQ per case)</h1>'
                        f'<div style="overflow-x:auto"><table>'
                        f'<tr style="color:#64748b"><td>agent</td>{hdr}</tr>{body}</table></div>')
    else:
        scores_table = ""
    pct_all = int(100 * n_done / total) if total else 0
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>agent_bench live</title>
<style>
 body{{font-family:ui-sans-serif,system-ui;background:#0b1220;color:#e2e8f0;margin:28px}}
 h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#64748b;font-size:12px;margin-bottom:18px}}
 .total{{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:14px 16px;margin-bottom:16px}}
 .bar{{background:#1e293b;border-radius:6px;height:8px;margin:8px 0;overflow:hidden}}
 .bar i{{display:block;height:100%;background:linear-gradient(90deg,#22d3ee,#22c55e)}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
 .card{{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px 14px}}
 .name{{font-weight:600;font-size:13.5px}} .nums{{font-size:12px;color:#94a3b8;margin:2px 0 6px}}
 .ok{{color:#22c55e}} .bad{{color:#ef4444}}
 .run{{font-size:12px;color:#22d3ee;font-variant-numeric:tabular-nums}}
 .run.stale{{color:#eab308}} .idle{{font-size:12px;color:#475569}}
 .jline{{font-size:12px;color:#a5b4fc;margin-top:4px}} .jline.dim,.dim{{color:#475569}}
 table{{width:100%;border-collapse:collapse;margin-top:18px;font-size:12px}}
 td{{padding:5px 8px;border-bottom:1px solid #1e293b;white-space:nowrap}}
 td.notes{{color:#64748b;white-space:normal}}
</style></head><body>
<h1>agent_bench — live run board</h1>
<div class="sub">auto-refreshes every 10s · {time.strftime('%H:%M:%S')} · manifest: {n_done} rows</div>
<div class="total"><b>{n_done} / {total}</b> tasks complete ({pct_all}%)
  <div class="bar"><i style="width:{pct_all}%"></i></div></div>
<div class="grid">{''.join(cards)}</div>
{scores_table}
<table><tr style="color:#64748b"><td>time</td><td>agent</td><td>case</td><td>status</td><td>dur</td><td>notes</td></tr>
{''.join(trs)}</table>
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
