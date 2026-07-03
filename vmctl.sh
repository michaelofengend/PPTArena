#!/usr/bin/env bash
# pull fixed agents.json (gemini trust env) and restart the runner; board untouched
cd /root/PPTArena
git fetch -q origin main && git reset -q --hard origin/main
grep -q GEMINI_CLI_TRUST_WORKSPACE agent_bench/agents.json && echo "agents.json has trust fix" || echo "WARN: fix missing"
pkill -f "run_agent[s].py" 2>/dev/null
pkill -f "codex exe[c]" 2>/dev/null; pkill -f "claude -[p]" 2>/dev/null; pkill -f "gemini -[m]" 2>/dev/null; pkill -f "opencode ru[n]" 2>/dev/null
sleep 3
set -a; . /root/PPTArena/credentials.env; set +a
cd /root/PPTArena
nohup python3 -u agent_bench/run_agents.py --parallel 5 >> agent_bench/run.log 2>&1 &
sleep 6
echo "runner: $(pgrep -fc 'run_agent[s].py') | preds so far: $(find agent_bench/predictions -name '*.pptx' | wc -l)"
echo "VMCTL_RESTART_DONE"
