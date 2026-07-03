#!/usr/bin/env bash
cd /root/PPTArena
git fetch -q origin main && git reset -q --hard origin/main
python3 - << 'PY'
import csv, os
from pathlib import Path
m = Path("agent_bench/predictions/manifest.csv")
latest = {}
with m.open() as fh:
    for r in csv.DictReader(fh):
        latest[(r["agent"], r["slug"])] = r
removed = 0
for (agent, slug), r in latest.items():
    if r["status"] == "no_change":
        p = Path("agent_bench/predictions") / agent / f"{slug}.pptx"
        if p.exists():
            p.unlink(); removed += 1
print("deleted", removed, "no_change placeholder predictions")
PY
pkill -f "run_agent[s].py" 2>/dev/null
pkill -f "codex exe[c]" 2>/dev/null; pkill -f "claude -[p]" 2>/dev/null; pkill -f "gemini -[m]" 2>/dev/null; pkill -f "opencode ru[n]" 2>/dev/null
sleep 3
set -a; . /root/PPTArena/credentials.env; set +a
nohup python3 -u agent_bench/run_agents.py --parallel 5 >> agent_bench/run.log 2>&1 &
sleep 6
echo "runner: $(pgrep -fc 'run_agent[s].py') | git: $(git log --oneline -1 | cut -c1-40)"
echo "VMCTL_ISOFIX_DONE"
