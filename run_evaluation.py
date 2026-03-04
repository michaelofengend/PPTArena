import json
import os
import sys
from pathlib import Path
import time
import tempfile
import base64

# Add src to python path so its modules can be imported
sys.path.insert(0, str(Path(__file__).parent / "src"))

from llm.judge import call_llm_judge
from ppt import (
    pptx_to_json,
    export_slides_to_images,
    extract_specific_xml_from_pptx
)

# Configuration
RESULTS_JSON_PATH = Path("benchmark_results.json")
EVAL_RESULTS_JSON_PATH = Path("evaluation_results_bulk.json")
EVAL_HTML_REPORT_PATH = Path("evaluation_report.html")
JUDGE_MODEL = "gpt-5.2-2025-12-11"

def image_to_base64(image_path):
    """Converts an image file to a base64 encoded string."""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error converting image {image_path} to base64: {e}")
        return None

def generate_eval_html_report(results):
    """Generates an HTML report from the evaluation results."""
    from datetime import datetime
    
    html_content = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "    <title>PPTArena Evaluation Report</title>",
        "    <style>",
        "        body { font-family: sans-serif; margin: 20px; color: #333; }",
        "        .case { border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 5px; }",
        "        h2 { color: #0056b3; margin-top: 0; }",
        "        .metadata { font-size: 0.9em; color: #666; margin-bottom: 10px; }",
        "        .section { margin-top: 15px; }",
        "        .section-title { font-weight: bold; margin-bottom: 5px; }",
        "        pre { background-color: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; font-size: 0.85em; }",
        "        .score { font-weight: bold; font-size: 1.2em; }",
        "        .high { color: green; }",
        "        .medium { color: orange; }",
        "        .low { color: red; }",
        "    </style>",
        "</head>",
        "<body>",
        "    <h1>PPTArena Evaluation Report</h1>",
        f"    <p>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
        f"    <p>Judge Model: {JUDGE_MODEL}</p>",
        f"    <p>Total Cases Evaluated: {len(results)}</p>",
        "    <hr>"
    ]

    for case in results:
        instr_score = case.get('instruction_following_score', 'N/A')
        vis_score = case.get('visual_quality_score', 'N/A')
        
        def get_score_class(s):
            if isinstance(s, (int, float)):
                return "high" if s >= 4 else "medium" if s >= 2 else "low"
            return ""

        instr_class = get_score_class(instr_score)
        vis_class = get_score_class(vis_score)

        html_content.append("<div class='case'>")
        html_content.append(f"    <h2>{case.get('name', 'Unknown Case')}</h2>")
        html_content.append("    <div class='metadata'>")
        html_content.append(f"        <strong>Original:</strong> {case.get('original', 'N/A')}<br>")
        html_content.append(f"        <strong>Modified:</strong> {case.get('benchmark_modified_filepath', 'N/A')}<br>")
        html_content.append(f"        <strong>LLM Engine Used:</strong> {case.get('llm_engine_used', 'N/A')}<br>")
        html_content.append("    </div>")
        
        html_content.append("    <div class='section'>")
        html_content.append("        <div class='section-title'>Instruction Following:</div>")
        html_content.append(f"        <p>Score: <span class='score {instr_class}'>{instr_score}</span> / 5</p>")
        html_content.append(f"        <p>Reason: {case.get('instruction_following_reason', 'N/A')}</p>")
        html_content.append("    </div>")

        html_content.append("    <div class='section'>")
        html_content.append("        <div class='section-title'>Visual Quality / Preservation:</div>")
        html_content.append(f"        <p>Score: <span class='score {vis_class}'>{vis_score}</span> / 5</p>")
        html_content.append(f"        <p>Reason: {case.get('visual_quality_reason', 'N/A')}</p>")
        html_content.append("    </div>")
        
        html_content.append("</div>")

    html_content.append("</body>")
    html_content.append("</html>")
    
    with open(EVAL_HTML_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(html_content))

def main():
    print(f"Starting PPTArena Bulk Evaluation with Judge Model: {JUDGE_MODEL}")
    
    # Load benchmark results
    try:
        with open(RESULTS_JSON_PATH, "r") as f:
            benchmark_results = json.load(f)
    except Exception as e:
        print(f"Error loading {RESULTS_JSON_PATH}: {e}")
        return

    # Load existing evaluation results to resume
    evaluation_results = []
    if EVAL_RESULTS_JSON_PATH.exists():
        try:
            with open(EVAL_RESULTS_JSON_PATH, "r") as f:
                content = f.read().strip()
                if content:
                    evaluation_results = json.loads(content)
                    print(f"Loaded {len(evaluation_results)} existing evaluation results. Resuming...")
        except Exception as e:
            print(f"Could not load existing evaluation results. Starting fresh. Error: {e}")
            evaluation_results = []

    evaluated_names = {r.get("name") for r in evaluation_results}
    
    from llm.utils import load_api_keys
    api_keys = load_api_keys()
    if not api_keys.get("openai"):
        print("WARNING: OpenAI API key is missing. Judging may fail. Please set it in credentials.env or as an environment variable (OPENAI_API_KEY).")

    for idx, case in enumerate(benchmark_results):
        case_name = case.get("name")
        status = case.get("status")
        
        if status != "Success":
            print(f"[{idx+1}/{len(benchmark_results)}] Skipping '{case_name}' - Benchmark run failed for this case.")
            continue
            
        if case_name in evaluated_names:
            print(f"[{idx+1}/{len(benchmark_results)}] Skipping '{case_name}' - Already evaluated.")
            continue
            
        original_rel_path = case.get("original")
        modified_abs_path = case.get("benchmark_modified_filepath")
        
        print(f"\n==================================================")
        print(f"[{idx+1}/{len(benchmark_results)}] Evaluating Case: {case_name}")
        
        if not modified_abs_path or not Path(modified_abs_path).exists():
            print(f"  ERROR: Modified PPTX file not found at {modified_abs_path}")
            continue

        original_abs_path = Path.cwd() / "data" / original_rel_path
        if not original_abs_path.exists():
             original_abs_path = Path.cwd() / "src" / "data" / original_rel_path
             if not original_abs_path.exists():
                 original_abs_path = Path.cwd() / original_rel_path
                 
        if not original_abs_path.exists():
            print(f"  ERROR: Original PPTX file not found at {original_abs_path}")
            continue

        print(f"Original PPTX: {original_abs_path.name}")
        print(f"Modified PPTX: {Path(modified_abs_path).name}")

        try:
            # 1. Convert to JSON
            gt_json = pptx_to_json(str(original_abs_path))
            pred_json = pptx_to_json(str(modified_abs_path))
            
            # Initial JSON is used as baseline. For our benchmark, original IS the initial before editing.
            # In some cases there's a separate GT and Initial, but benchmark uses original_rel_path.
            initial_json = pptx_to_json(str(original_abs_path))

            # 2. Export to Images
            with tempfile.TemporaryDirectory() as tmpdir:
                gt_dir = Path(tmpdir) / "ground_truth"
                pred_dir = Path(tmpdir) / "prediction"
                
                gt_imgs = export_slides_to_images(str(original_abs_path), str(gt_dir))
                pred_imgs = export_slides_to_images(str(modified_abs_path), str(pred_dir))
                
                gt_b64_all = [image_to_base64(p) for p in gt_imgs if p]
                pred_b64_all = [image_to_base64(p) for p in pred_imgs if p]
                initial_b64_all = gt_b64_all # using original as initial
            
            # 3. Call Judge
            prompt_instruction = case.get("prompt", "")
            style_target = case.get("style_target", "")
            full_prompt = f"Instruction: {prompt_instruction}\nStyle Target: {style_target}"
            
            print("  Calling LLM Judge...")
            start_time = time.time()
            judge_result = call_llm_judge(
                user_prompt=full_prompt,
                initial_ppt_json=initial_json,
                original_ppt_json=gt_json,
                modified_ppt_json=pred_json,
                initial_slide_images_b64=initial_b64_all,
                original_slide_images_b64=gt_b64_all,
                modified_slide_images_b64=pred_b64_all,
                original_slide_xml="", # Optional for this step
                modified_slide_xml="", # Optional for this step
                judge_model=JUDGE_MODEL,
                evaluation_mode='benchmark',
                api_keys=api_keys
            )
            eval_time = time.time() - start_time
            
            if judge_result and not judge_result.get('error'):
                print(f"  SUCCESS in {eval_time:.1f}s")
                print(f"  Instruction Score: {judge_result.get('instruction_following_score')}")
                print(f"  Visual Score: {judge_result.get('visual_quality_score')}")
                
                # Merge judge results into the case object
                eval_case = case.copy()
                eval_case.update(judge_result)
                evaluation_results.append(eval_case)
            else:
                print(f"  ERROR from judge: {judge_result.get('error', 'Unknown error')}")
                # Save anyway to note that judging failed
                eval_case = case.copy()
                eval_case["error_judging"] = judge_result.get('error', 'Unknown error')
                evaluation_results.append(eval_case)
                
        except Exception as e:
            print(f"  FATAL ERROR during judging: {e}")
            eval_case = case.copy()
            eval_case["error_judging"] = str(e)
            evaluation_results.append(eval_case)
            import traceback
            traceback.print_exc()
            
        # Add to results and save incrementally
        with open(EVAL_RESULTS_JSON_PATH, "w") as f:
            json.dump(evaluation_results, f, indent=2)
            
        # Generate HTML report incrementally
        generate_eval_html_report(evaluation_results)

    print(f"\n==================================================")
    print("Evaluation Complete!")
    print(f"Total Evaluated: {len(evaluation_results)}")
    
    print(f"Outputs saved to {EVAL_RESULTS_JSON_PATH} and {EVAL_HTML_REPORT_PATH}")

if __name__ == "__main__":
    main()
