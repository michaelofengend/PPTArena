#!/usr/bin/env python3
"""
Score agent_bench predictions with the standard PPTArena arena judge.

Reads predictions from agent_bench/predictions/<agent_id>/<case_slug>.pptx
(produced by run_agents.py), judges each against the ground truth, and writes
agent_bench/results/<agent_id>_judge_results.csv in the same schema as the
existing judge scripts — the webapp leaderboard picks these files up
automatically (sources are pre-registered in src/app.py).

Cases in the split that have no prediction get zero-score rows, so every CSV
always covers the full split (missing work is penalized, not hidden).

Usage:
    python3 agent_bench/judge_predictions.py --agents all
    python3 agent_bench/judge_predictions.py --agents codex_gpt55 --max-workers 4

Requires credentials.env with OPENAI_API_KEY (judge model) and LibreOffice
for slide rendering — scoring can run on any machine with the repo, it does
not need the agent CLIs.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

AGENT_BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_BENCH_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import llm_handler  # noqa: E402
from ppt import (  # noqa: E402
    export_slides_to_images,
    extract_specific_xml_from_pptx,
    pptx_to_json,
)

from run_agents import slugify  # noqa: E402

PAIRS_PATH = SRC_DIR / "evaluation_pairs_refined.json"
SUBSET_PATH = AGENT_BENCH_DIR / "subset25.json"
AGENTS_PATH = AGENT_BENCH_DIR / "agents.json"
PREDICTIONS_ROOT = AGENT_BENCH_DIR / "predictions"
RESULTS_ROOT = AGENT_BENCH_DIR / "results"

DEFAULT_JUDGE_MODEL = "gpt-5.1-2025-11-13"

CSV_FIELDS = [
    "case_index",
    "case_name",
    "category",
    "prediction",
    "instruction_following_score",
    "visual_quality_score",
    "instruction_following_reason",
    "visual_quality_reason",
    "judge_model",
    "judge_time_seconds",
    "errors",
]


def image_to_base64(image_path: str) -> str | None:
    try:
        with open(image_path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("utf-8")
    except Exception:
        return None


def extract_case_index(name: str) -> int:
    try:
        prefix, _ = name.split(":", 1)
        return int(prefix.strip().split()[1])
    except Exception:
        return -1


def normalize_category(raw) -> str:
    if isinstance(raw, list):
        return ", ".join(str(v) for v in raw if v)
    return str(raw) if raw else "Unknown"


def compose_prompt(prompt: str, style_target: str) -> str:
    instruction = (prompt or "").strip()
    style = (style_target or "").strip()
    if not instruction.startswith("Instruction:"):
        instruction = f"Instruction: {instruction}"
    if style:
        if not style.startswith("Style Target:"):
            style = f"Style Target: {style}"
        return f"{instruction}\n{style}"
    return instruction


def zero_row(case: dict, prediction: Path | None, reason: str, judge_model: str) -> dict:
    return {
        "case_index": extract_case_index(case["name"]),
        "case_name": case["name"],
        "category": normalize_category(case.get("category")),
        "prediction": str(prediction) if prediction else "",
        "instruction_following_score": "0.00",
        "visual_quality_score": "0.00",
        "instruction_following_reason": reason,
        "visual_quality_reason": reason,
        "judge_model": judge_model,
        "judge_time_seconds": "0.000",
        "errors": reason,
    }


def judge_case(case: dict, prediction_path: Path, api_key: str, judge_model: str) -> dict:
    initial_path = (PROJECT_ROOT / case["original"]).resolve()
    ground_truth_path = (PROJECT_ROOT / case["ground_truth"]).resolve()

    try:
        init_json = pptx_to_json(str(initial_path))
        gt_json = pptx_to_json(str(ground_truth_path))
        pred_json = pptx_to_json(str(prediction_path))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            gt_imgs = export_slides_to_images(str(ground_truth_path), str(tmp / "gt"))
            pred_imgs = export_slides_to_images(str(prediction_path), str(tmp / "pred"))
            gt_b64 = [b for b in (image_to_base64(p) for p in gt_imgs) if b]
            pred_b64 = [b for b in (image_to_base64(p) for p in pred_imgs) if b]

        gt_xml = extract_specific_xml_from_pptx(str(ground_truth_path), "ppt/slides/slide1.xml") or ""
        pred_xml = extract_specific_xml_from_pptx(str(prediction_path), "ppt/slides/slide1.xml") or ""
    except Exception as exc:
        return zero_row(case, prediction_path, f"Artifact prep error: {exc}", judge_model)

    start = time.time()
    try:
        response = llm_handler.call_llm_judge(
            user_prompt=compose_prompt(case.get("prompt", ""), case.get("style_target", "")),
            judge_model=judge_model,
            initial_ppt_json=init_json,
            original_ppt_json=gt_json,
            modified_ppt_json=pred_json,
            original_slide_images_b64=gt_b64,
            modified_slide_images_b64=pred_b64,
            original_slide_xml=gt_xml,
            modified_slide_xml=pred_xml,
            evaluation_mode="arena",
            api_key=api_key,
        )
    except Exception as exc:
        return zero_row(case, prediction_path, f"Judge call failed: {exc}", judge_model)

    judge_time = round(time.time() - start, 3)
    if not isinstance(response, dict) or response.get("error"):
        reason = response.get("error", "Invalid judge response.") if isinstance(response, dict) else "Invalid judge response."
        row = zero_row(case, prediction_path, reason, judge_model)
        row["judge_time_seconds"] = f"{judge_time:.3f}"
        return row

    return {
        "case_index": extract_case_index(case["name"]),
        "case_name": case["name"],
        "category": normalize_category(case.get("category")),
        "prediction": str(prediction_path),
        "instruction_following_score": f"{float(response.get('instruction_following_score') or 0.0):.2f}",
        "visual_quality_score": f"{float(response.get('visual_quality_score') or 0.0):.2f}",
        "instruction_following_reason": (response.get("instruction_following_reason") or "").strip()
        or "No instruction reasoning returned.",
        "visual_quality_reason": (response.get("visual_quality_reason") or "").strip()
        or "No visual reasoning returned.",
        "judge_model": judge_model,
        "judge_time_seconds": f"{judge_time:.3f}",
        "errors": "",
    }


def judge_agent(agent_id: str, cases: list[dict], api_key: str, judge_model: str,
                max_workers: int, resume: bool) -> None:
    output_path = RESULTS_ROOT / f"{agent_id}_judge_results.csv"
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if resume and output_path.exists():
        with output_path.open(newline="", encoding="utf-8") as fh:
            done = {row["case_name"] for row in csv.DictReader(fh)}
    else:
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=CSV_FIELDS).writeheader()

    todo = [c for c in cases if c["name"] not in done]
    print(f"\n=== {agent_id}: judging {len(todo)} case(s) "
          f"({len(done)} already in {output_path.name}) ===")
    if not todo:
        return

    lock = Lock()

    def emit(row: dict) -> None:
        with lock:
            with output_path.open("a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=CSV_FIELDS).writerow(row)
        print(f"[Case {row['case_index']:>3}] IF {row['instruction_following_score']} | "
              f"VQ {row['visual_quality_score']}  ({agent_id})"
              + (f"  !! {row['errors']}" if row["errors"] else ""))

    def work(case: dict) -> dict:
        prediction = PREDICTIONS_ROOT / agent_id / f"{slugify(case['name'])}.pptx"
        if not prediction.exists():
            return zero_row(case, prediction, "Prediction PPTX missing for this case.", judge_model)
        return judge_case(case, prediction, api_key, judge_model)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(work, case): case for case in todo}
        for future in as_completed(futures):
            case = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001
                row = zero_row(case, None, f"Unhandled judge exception: {exc}", judge_model)
            emit(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge agent_bench predictions with the arena judge.")
    parser.add_argument("--agents", default="all",
                        help="Comma-separated agent ids from agents.json, or 'all' (default).")
    parser.add_argument("--split", choices=["subset", "full"], default="subset",
                        help="Judge the matched 25-case subset (default) or all 100 cases.")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                        help=f"Judge model id (default: {DEFAULT_JUDGE_MODEL}).")
    parser.add_argument("--max-workers", type=int, default=4,
                        help="Concurrent judge calls per agent (default: 4).")
    parser.add_argument("--no-resume", action="store_true",
                        help="Rewrite result CSVs from scratch instead of appending missing cases.")
    args = parser.parse_args()

    agents = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    agent_ids = list(agents) if args.agents == "all" else [a.strip() for a in args.agents.split(",")]
    unknown = [a for a in agent_ids if a not in agents]
    if unknown:
        sys.exit(f"Unknown agent id(s): {', '.join(unknown)}. Known: {', '.join(agents)}")

    pairs = json.loads(PAIRS_PATH.read_text(encoding="utf-8"))
    for pair in pairs:
        pair["name"] = pair["name"].strip()
    if args.split == "subset":
        subset_names = {n.strip() for n in json.loads(SUBSET_PATH.read_text(encoding="utf-8"))}
        pairs = [p for p in pairs if p["name"] in subset_names]
    pairs.sort(key=lambda c: extract_case_index(c["name"]))

    keys = llm_handler.load_api_keys()
    api_key = keys.get("openai_api_key")
    if not api_key:
        sys.exit("OPENAI_API_KEY missing from credentials.env; required for judging.")

    for agent_id in agent_ids:
        judge_agent(agent_id, pairs, api_key, args.judge_model,
                    args.max_workers, resume=not args.no_resume)

    print("\nAll done. Result CSVs are in agent_bench/results/ — commit them and the "
          "leaderboard rows appear automatically (sources pre-registered in src/app.py).")


if __name__ == "__main__":
    main()
