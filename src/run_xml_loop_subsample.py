#!/usr/bin/env python3
"""
Run the PPTPilot XML editing pipeline in Loop3x mode on the 25-case subsample
and immediately judge each output with GPT-5. Results append to a CSV.

Usage:
    python src/run_xml_loop_subsample.py
    python src/run_xml_loop_subsample.py --pairs-path ChatGPTAgentSamples/evaluation_pairs_chatgpt_agent_samples.json \\
        --results-csv src/benchmark_runs/pptpilot_xml_loop_results.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import llm_handler
import orchestrator


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_PAIRS_PATH = PROJECT_ROOT / "ChatGPTAgentSamples" / "evaluation_pairs_chatgpt_agent_samples.json"
DEFAULT_RESULTS_CSV = SCRIPT_DIR / "benchmark_runs" / "pptpilot_xml_loop_subsample_results.csv"
EDITOR_MODEL = "gpt-5.1-2025-11-13"
JUDGE_MODEL = "gpt-5.1-2025-11-13"
CSV_FIELDS = [
    "timestamp",
    "case_index",
    "case_name",
    "llm_engine",
    "generation_time_seconds",
    "loop_iterations",
    "prediction_path",
    "judge_model",
    "judge_time_seconds",
    "instruction_following_score",
    "visual_quality_score",
    "instruction_following_reason",
    "visual_quality_reason",
]


class CaseRunError(RuntimeError):
    """Raised when either the editor or judge fails for a given case."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PPTPilot XML Loop3x on the subsample pairs and judge outputs.")
    parser.add_argument(
        "--pairs-path",
        type=Path,
        default=DEFAULT_PAIRS_PATH,
        help="Path to the 25-case evaluation JSON file.",
    )
    parser.add_argument(
        "--results-csv",
        type=Path,
        default=DEFAULT_RESULTS_CSV,
        help="CSV path to append new results.",
    )
    parser.add_argument(
        "--loop-iterations",
        type=int,
        default=3,
        help="How many XML-loop iterations to run per case (default: 3).",
    )
    parser.add_argument(
        "--case-filter",
        type=int,
        nargs="*",
        help="Optional list of case indices to run (defaults to all 25).",
    )
    parser.add_argument(
        "--editor-model",
        default=EDITOR_MODEL,
        help=f"Editor model id for the XML loop (default: {EDITOR_MODEL}).",
    )
    parser.add_argument(
        "--judge-model",
        default=JUDGE_MODEL,
        help=f"Judge model id (default: {JUDGE_MODEL}); ignored with --skip-judge.",
    )
    parser.add_argument(
        "--collect-dir",
        type=Path,
        default=None,
        help="Also copy each final prediction to <dir>/case_XXX.pptx "
             "(agent_bench/judge_predictions.py judges such a directory).",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Generate predictions only; judge separately (e.g. median-of-3 via agent_bench).",
    )
    return parser.parse_args()


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()


def load_cases(pairs_path: Path, allowed_indices: Optional[Iterable[int]]) -> List[dict]:
    if not pairs_path.exists():
        raise FileNotFoundError(f"Pairs file not found: {pairs_path}")
    raw_cases = json.loads(pairs_path.read_text(encoding="utf-8"))
    selected = []
    allowed = set(allowed_indices) if allowed_indices else None
    for entry in raw_cases:
        idx = extract_case_index(entry.get("name", ""))
        if idx <= 0:
            continue
        if allowed and idx not in allowed:
            continue
        selected.append(entry)
    return selected


def extract_case_index(name: str) -> int:
    try:
        prefix, _ = name.split(":", 1)
        return int(prefix.strip().split()[1])
    except Exception:
        return -1


def compose_judge_prompt(prompt: str, style_target: str) -> str:
    instruction = (prompt or "").strip()
    style = (style_target or "").strip()
    if instruction and not instruction.lower().startswith("instruction:"):
        instruction = f"Instruction: {instruction}"
    if style:
        if not style.lower().startswith("style target:"):
            style = f"Style Target: {style}"
        return f"{instruction}\n{style}" if instruction else style
    return instruction


def ensure_editor_key(editor_model: str) -> str:
    """Resolve the API key for the editor model's provider."""
    keys = llm_handler.load_api_keys()
    lowered = (editor_model or "").lower()
    if "kimi" in lowered or "moonshot" in lowered:
        api_key = keys.get("moonshot") or keys.get("kimi") or keys.get("kimi_api_key")
        missing = "MOONSHOT_API_KEY"
    elif any(s in lowered for s in ("gemini", "gemma", "google")):
        api_key = keys.get("gemini") or keys.get("gemini_api_key")
        missing = "GEMINI_API_KEY"
    else:
        api_key = keys.get("openai") or keys.get("openai_api_key")
        missing = "OPENAI_API_KEY"
    if not api_key:
        raise RuntimeError(f"{missing} missing from credentials.env")
    return api_key


def run_xml_loop_iteration(
    original_path: Path,
    prompt_text: str,
    loop_iterations: int,
    api_key: str,
) -> Path:
    """Run the XML editing path repeatedly, feeding each output into the next iteration."""
    current_input = str(original_path)
    session_id = f"xml-loop-{uuid.uuid4().hex}"
    loop_iterations = max(1, loop_iterations)
    last_result: Optional[Dict] = None

    for iteration in range(1, loop_iterations + 1):
        request_id = f"{session_id}-iter{iteration}"
        result = orchestrator._execute_xml_edit(  # type: ignore[attr-defined]
            original_filepath=current_input,
            prompt_text=prompt_text,
            selected_model_id=EDITOR_MODEL,
            use_pre_analysis=False,
            request_id=request_id,
            api_key=api_key,
            session_id=session_id,
        )
        if not isinstance(result, dict):
            raise CaseRunError(f"XML edit returned an unexpected payload on iteration {iteration}.")
        if result.get("error"):
            raise CaseRunError(f"XML edit failed on iteration {iteration}: {result['error']}")
        modified = result.get("modified_pptx_filepath")
        if not modified:
            raise CaseRunError(f"XML edit iteration {iteration} produced no PPTX output.")
        resolved = Path(modified).resolve()
        if not resolved.exists():
            raise CaseRunError(f"XML edit iteration {iteration} output missing at {resolved}")
        current_input = str(resolved)
        last_result = result

    if not last_result:
        raise CaseRunError("XML loop produced no results.")
    return Path(current_input).resolve()


def run_judge(prompt: str, artifacts: dict, api_key: str) -> tuple[float, float, str, str, float]:
    judge_start = time.time()
    response = llm_handler.call_llm_judge(
        user_prompt=prompt,
        judge_model=JUDGE_MODEL,
        initial_ppt_json=artifacts["initial_json"],
        original_ppt_json=artifacts["ground_truth_json"],
        modified_ppt_json=artifacts["prediction_json"],
        initial_slide_images_b64=artifacts["initial_images_b64"],
        original_slide_images_b64=artifacts["ground_truth_images_b64"],
        modified_slide_images_b64=artifacts["prediction_images_b64"],
        original_slide_xml=artifacts["ground_truth_xml"],
        modified_slide_xml=artifacts["prediction_xml"],
        evaluation_mode="arena",
        api_key=api_key,
    )
    judge_time = round(time.time() - judge_start, 3)
    if not isinstance(response, dict) or response.get("error"):
        raise CaseRunError(f"Judge error: {response.get('error', 'unknown response')}")
    return (
        float(response.get("instruction_following_score") or 0.0),
        float(response.get("visual_quality_score") or 0.0),
        response.get("instruction_following_reason", ""),
        response.get("visual_quality_reason", ""),
        judge_time,
    )


def append_row(csv_path: Path, row: Dict[str, str]) -> None:
    with csv_path.open("a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writerow(row)


def execute_case(entry: dict, loop_iterations: int, api_key: str,
                 collect_dir: Optional[Path] = None, skip_judge: bool = False) -> Dict[str, str]:
    case_name = entry.get("name", "Unnamed Case")
    case_index = extract_case_index(case_name)
    original_path = (PROJECT_ROOT / entry["original"]).resolve()
    ground_truth_path = (PROJECT_ROOT / entry["ground_truth"]).resolve()

    if not original_path.exists():
        raise CaseRunError(f"Original PPTX missing: {original_path}")
    if not ground_truth_path.exists():
        raise CaseRunError(f"Ground truth PPTX missing: {ground_truth_path}")

    edit_start = time.time()
    prediction_path = run_xml_loop_iteration(
        original_path=original_path,
        prompt_text=entry.get("prompt", ""),
        loop_iterations=loop_iterations,
        api_key=api_key,
    )
    generation_time = round(time.time() - edit_start, 3)

    if collect_dir is not None:
        collect_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(prediction_path, collect_dir / f"case_{case_index:03d}.pptx")

    if skip_judge:
        instruction_score = visual_score = 0.0
        instruction_reason = visual_reason = "(not judged in this run)"
        judge_time = 0.0
    else:
        from judge_chatgpt_agent_samples import prepare_judge_artifacts
        artifacts = prepare_judge_artifacts(original_path, ground_truth_path, prediction_path)
        judge_prompt = compose_judge_prompt(entry.get("prompt", ""), entry.get("style_target", ""))
        (
            instruction_score,
            visual_score,
            instruction_reason,
            visual_reason,
            judge_time,
        ) = run_judge(judge_prompt, artifacts, api_key)

    return {
        "timestamp": datetime.now().isoformat(),
        "case_index": str(case_index),
        "case_name": case_name,
        "llm_engine": EDITOR_MODEL,
        "generation_time_seconds": f"{generation_time:.3f}",
        "loop_iterations": str(loop_iterations),
        "prediction_path": str(prediction_path),
        "judge_model": JUDGE_MODEL,
        "judge_time_seconds": f"{judge_time:.3f}",
        "instruction_following_score": f"{instruction_score:.2f}",
        "visual_quality_score": f"{visual_score:.2f}",
        "instruction_following_reason": instruction_reason,
        "visual_quality_reason": visual_reason,
    }


def main() -> None:
    global EDITOR_MODEL, JUDGE_MODEL
    args = parse_args()
    EDITOR_MODEL = args.editor_model
    JUDGE_MODEL = args.judge_model
    cases = load_cases(args.pairs_path.resolve(), args.case_filter)
    if not cases:
        raise SystemExit("No cases matched the requested filters.")

    api_key = ensure_editor_key(EDITOR_MODEL)
    ensure_csv(args.results_csv.resolve())
    print(f"Running XML Loop{args.loop_iterations} on {len(cases)} cases "
          f"(editor: {EDITOR_MODEL}{', generate-only' if args.skip_judge else f', judge: {JUDGE_MODEL}'})...",
          flush=True)

    for entry in cases:
        case_name = entry.get("name", "Unnamed Case")
        try:
            row = execute_case(entry, args.loop_iterations, api_key,
                               collect_dir=args.collect_dir, skip_judge=args.skip_judge)
            append_row(args.results_csv.resolve(), row)
            done = f"generated in {row['generation_time_seconds']}s" if args.skip_judge else (
                f"IF {row['instruction_following_score']}, VQ {row['visual_quality_score']}")
            print(f"  ✓ {case_name} -> {done}", flush=True)
        except CaseRunError as exc:
            print(f"  ✗ {case_name} failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
