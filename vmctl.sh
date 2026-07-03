#!/usr/bin/env bash
# EMERGENCY: stop all generation (opencode retries still failing); start judging done agents
pkill -f "run_agent[s].py" 2>/dev/null
pkill -f "opencode ru[n]" 2>/dev/null; pkill -f "codex exe[c]" 2>/dev/null; pkill -f "gemini -[m]" 2>/dev/null; pkill -f "claude -[p]" 2>/dev/null
sleep 2
echo "stopped: runner=$(pgrep -fc 'run_agent[s].py' || true) agents=$(pgrep -fc 'opencode ru[n]|codex exe[c]|gemini -[m]|claude -[p]' || true)"
cd /root/PPTArena
git fetch -q origin main && git reset -q --hard origin/main
set -a; . ./credentials.env; set +a
rm -f agent_bench/results/*_judge_results.csv
nohup python3 -u agent_bench/judge_predictions.py --agents claude_code_opus48,codex_gpt55 --samples 3 > agent_bench/judge.log 2>&1 &
pkill -f "status_boar[d].py" 2>/dev/null; sleep 1
nohup python3 agent_bench/status_board.py --host 0.0.0.0 --port 80 > agent_bench/board.log 2>&1 &
echo "judge: $(pgrep -fc 'judge_prediction[s].py') board: $(pgrep -fc 'status_boar[d].py')"
echo "VMCTL_STOP_AND_JUDGE_DONE"
