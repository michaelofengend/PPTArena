# agent_bench — CLI coding agents on PPTArena

Scaffolding for running six CLI coding agents across the PPTArena benchmark on a VM.
Generation and scoring are **decoupled**: the VM only generates predictions; judging
can run later on any machine with the repo and an OpenAI key.

| agent id | System | Backbone |
|---|---|---|
| `codex_gpt55` | Codex CLI | GPT-5.5, reasoning effort xhigh |
| `claude_code_opus48` | Claude Code | Opus 4.8 (Max plan) |
| `opencode_glm52` | OpenCode | GLM-5.2 |
| `gemini_cli_35flash` | Gemini CLI | Gemini 3.5 Flash |
| `opencode_minimax_m3` | OpenCode | MiniMax-M3 |
| `opencode_deepseek_v4` | OpenCode | DeepSeek V4 Pro |

Exact CLI flags and model ids live in [`agents.json`](agents.json) — tweak there if a
CLI has drifted; no code changes needed. Leaderboard entries for all six are already
registered in `src/app.py`: rows appear automatically once result CSVs land in
`agent_bench/results/`.

## 1. VM setup

```bash
git clone https://github.com/michaelofengend/PPTArena.git && cd PPTArena
python3 -m pip install -r src/requirements.txt   # only needed for scoring, but cheap

# Install the agent CLIs (names current as of mid-2026; verify if installs fail)
npm install -g @openai/codex            # codex
npm install -g @anthropic-ai/claude-code  # claude
npm install -g opencode-ai              # opencode  (or: curl -fsSL https://opencode.ai/install | bash)
npm install -g @google/gemini-cli       # gemini
```

## 2. Authenticate each CLI (Michael provides the accounts)

```bash
codex login            # ChatGPT account with GPT-5.5 access
claude                 # first run opens login; use the Max-plan account
opencode auth login    # run three times: Zhipu/Z.AI, MiniMax, DeepSeek providers
gemini                 # first run opens Google OAuth
```

Then verify everything is wired up:

```bash
python3 agent_bench/run_agents.py --check
```

If an OpenCode model id doesn't match, list what's available (`opencode models`)
and fix the `-m` value in `agents.json`.

## 3. Smoke test, then the real run

```bash
# One case through one agent, watch it work:
python3 agent_bench/run_agents.py --agents codex_gpt55 --limit 1

# Full 25-case hard subset, all six agents, five processes at a time:
nohup python3 agent_bench/run_agents.py --parallel 5 > agent_bench/run.log 2>&1 &
tail -f agent_bench/run.log
```

- **Resume-safe**: re-running skips any (agent, case) that already has a prediction;
  `--force` regenerates.
- Each task runs in an isolated `agent_bench/workdirs/<agent>/<case>/` containing
  `deck.pptx` (copy of the original) + `INSTRUCTION.md`; the agent's stdout is kept
  in `agent_output.log` there.
- Collected decks land in `agent_bench/predictions/<agent>/<case>.pptx`, with a
  status manifest at `agent_bench/predictions/manifest.csv` (statuses: `ok`,
  `no_change`, `timeout`, `invalid_pptx`, `cli_missing`, ...). `no_change` means the
  agent exited without touching the deck — worth re-running those with `--force`.
- Predictions/workdirs are gitignored (heavy binaries). To get them off the VM:
  `tar czf predictions.tgz agent_bench/predictions/` and download, or push them to
  the Hugging Face dataset.

## 4. Score later (any machine with the repo)

Judging defaults to **Gemini 3.5 Flash**. Needs `credentials.env` at the repo root
with `GEMINI_API_KEY` (or `OPENAI_API_KEY` if you pass a `gpt-*` judge) and
LibreOffice installed (slide rendering).

```bash
python3 agent_bench/judge_predictions.py --agents all              # single-sample
python3 agent_bench/judge_predictions.py --agents all --samples 3  # median of 3 (steadier)
```

Speed notes: ground-truth renders/JSON/XML are computed once per case and cached
(`benchmark_outputs/judge_render_cache/`), then shared across all six agents and
re-runs — only prediction decks are rendered per judgement. Flash is cheap enough
that `--samples 3` is the recommended setting for leaderboard numbers.

Writes `agent_bench/results/<agent_id>_judge_results.csv` (same schema as the
existing judge runs; subset cases without a prediction get zero rows so coverage is
always 25/25). Commit the CSVs and push — the leaderboard picks them up
automatically, on the deployed site too.

Before the first real scoring run, audit the benchmark data itself:

```bash
python3 src/audit_cases.py            # integrity checks + HTML review report
```

## 5. Decision gate

After scoring, compare against PPTPilot on the same subset (currently 38.8 for the
Gemini 3.1 Pro backbone, 30.4 for GPT-5.2). If **every** agent in the cohort beats
both PPTPilot rows, PPTPilot can be retired from the headline leaderboard (kept in
the paper/history). See `ROADMAP.md`.
