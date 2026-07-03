#!/usr/bin/env bash
# proper regeneration relaunch: purge placeholder preds, pull, single runner, new board
pkill -f "run_agent[s].py" 2>/dev/null; pkill -f "opencode ru[n]" 2>/dev/null; pkill -f "gemini -[m]" 2>/dev/null; sleep 2
cd /root/PPTArena
git fetch -q origin main && git reset -q --hard origin/main
python3 - << 'PY'
import csv
from pathlib import Path
latest = {}
with Path("agent_bench/predictions/manifest.csv").open() as fh:
    for r in csv.DictReader(fh):
        latest[(r["agent"], r["slug"])] = r
removed = 0
for (agent, slug), r in latest.items():
    if r["status"] != "ok":
        p = Path("agent_bench/predictions") / agent / f"{slug}.pptx"
        if p.exists(): p.unlink(); removed += 1
print("purged", removed, "non-ok placeholder predictions")
PY
set -a; . ./credentials.env; set +a
nohup python3 -u agent_bench/run_agents.py --parallel 5 > agent_bench/run.log 2>&1 &
pkill -f "status_boar[d].py" 2>/dev/null; sleep 1
nohup python3 agent_bench/status_board.py --host 0.0.0.0 --port 80 > agent_bench/board.log 2>&1 &
sleep 6
head -1 agent_bench/run.log
echo "runner: $(pgrep -f 'run_agent[s].py' | wc -l) board: $(pgrep -f 'status_boar[d].py' | wc -l)"
echo "VMCTL_REGEN_DONE"
