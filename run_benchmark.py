import json
import os
import sys
from pathlib import Path

# Add src to python path so its modules can be imported
sys.path.insert(0, str(Path(__file__).parent / "src"))

import orchestrator
from llm import utils as llm_utils
import time
from datetime import datetime
import shutil

# Configuration
BENCHMARK_JSON_PATH = Path("src/evaluation_pairs_refined.json")
RESULTS_JSON_PATH = Path("benchmark_results.json")
REPORT_HTML_PATH = Path("benchmark_report.html")
MODEL_ID = "gemini-3.1-pro-preview"
OUTPUT_DIR = Path("benchmark_outputs")
MAX_CASES = None # Set to an integer to test a smaller batch, e.g., 2
RETRY_FAILED = True # If True, previously failed cases will be re-run

def generate_html_report(results):
    """Generates an HTML report from the benchmark results."""
    html_content = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "    <title>PPTArena Benchmark Report</title>",
        "    <style>",
        "        body { font-family: sans-serif; margin: 20px; color: #333; }",
        "        .case { border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 5px; }",
        "        h2 { color: #0056b3; margin-top: 0; }",
        "        .metadata { font-size: 0.9em; color: #666; margin-bottom: 10px; }",
        "        .section { margin-top: 15px; }",
        "        .section-title { font-weight: bold; margin-bottom: 5px; }",
        "        pre { background-color: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; font-size: 0.85em; }",
        "        .error { color: red; font-weight: bold; }",
        "        .success { color: green; font-weight: bold; }",
        "    </style>",
        "</head>",
        "<body>",
        "    <h1>PPTArena Benchmark Report</h1>",
        f"    <p>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
        f"    <p>Model: {MODEL_ID}</p>",
        f"    <p>Total Cases Attempted: {len(results)}</p>",
        "    <hr>"
    ]

    for case in results:
        status = case.get('status', 'Unknown')
        status_class = "success" if status == "Success" else "error"
        
        html_content.append("<div class='case'>")
        html_content.append(f"    <h2>{case.get('name', 'Unknown Case')}</h2>")
        html_content.append("    <div class='metadata'>")
        html_content.append(f"        <strong>Original:</strong> {case.get('original', 'N/A')}<br>")
        html_content.append(f"        <strong>Status:</strong> <span class='{status_class}'>{status}</span><br>")
        html_content.append(f"        <strong>Processing Time:</strong> {case.get('timing_stats', {}).get('total_processing_time_s', 'N/A')} s<br>")
        html_content.append(f"        <strong>LLM Engine Used:</strong> {case.get('llm_engine_used', 'N/A')}<br>")
        
        # Determine strategy if not explicitly reported by looking at outputs
        strategy = "Unknown"
        if "generated_code" in case:
            strategy = "PYTHON_PPTX_EDIT"
        elif "modified_xml_data" in case and case["modified_xml_data"]:
            strategy = "XML_EDIT"
            
        html_content.append(f"        <strong>Likely Strategy Used:</strong> {strategy}<br>")
        html_content.append("    </div>")
        
        html_content.append("    <div class='section'>")
        html_content.append("        <div class='section-title'>Prompt:</div>")
        html_content.append(f"        <pre>{case.get('prompt', 'N/A')}</pre>")
        html_content.append("    </div>")

        if case.get('error'):
            html_content.append("    <div class='section'>")
            html_content.append("        <div class='section-title'>Error:</div>")
            html_content.append(f"        <pre class='error'>{case['error']}</pre>")
            html_content.append("    </div>")
            
        if strategy == "PYTHON_PPTX_EDIT":
            html_content.append("    <div class='section'>")
            html_content.append("        <div class='section-title'>Generated Python Code:</div>")
            html_content.append(f"        <pre>{case.get('generated_code', 'No code generated')}</pre>")
            html_content.append("    </div>")
            html_content.append("    <div class='section'>")
            html_content.append("        <div class='section-title'>Generated Content (JSON):</div>")
            html_content.append(f"        <pre>{json.dumps(case.get('generated_content', {}), indent=2)}</pre>")
            html_content.append("    </div>")
        elif strategy == "XML_EDIT":
            html_content.append("    <div class='section'>")
            html_content.append("        <div class='section-title'>Modified XML Files:</div>")
            xml_data = case.get('modified_xml_data', {})
            if xml_data:
                for filename, xml_content in xml_data.items():
                    html_content.append(f"        <strong>{filename}:</strong>")
                    html_content.append(f"        <pre>{xml_content}</pre>")
            else:
                html_content.append("        <p>No XML modifications found.</p>")
            html_content.append("    </div>")
            
        html_content.append("    <div class='section'>")
        html_content.append("        <div class='section-title'>Raw LLM Response:</div>")
        html_content.append(f"        <pre>{case.get('llm_response', 'No response')}</pre>")
        html_content.append("    </div>")

        html_content.append("</div>")

    html_content.append("</body>")
    html_content.append("</html>")
    
    with open(REPORT_HTML_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(html_content))
    print(f"\\nReport saved to {REPORT_HTML_PATH}")

def main():
    print("Starting PPTArena Bulk Evaluation")
    
    # Ensure output directory exists for modified PPTXs
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    # Load API keys
    api_keys = llm_utils.load_api_keys()
    if "gemini" not in api_keys:
        print("Warning: GEMINI_API_KEY not found in credentials.env or environment variables. Using fallback.")
        api_keys["gemini"] = "AIzaSyDs_ZT-I97kC_cNtJixpqxyiPXnUfL0GVI"

    # Load benchmark pairs
    try:
        with open(BENCHMARK_JSON_PATH, "r") as f:
            pairs = json.load(f)
    except Exception as e:
        print(f"Error loading {BENCHMARK_JSON_PATH}: {e}")
        return

    if MAX_CASES:
        pairs = pairs[:MAX_CASES]
        print(f"Limiting execution to the first {MAX_CASES} cases.")

    results = []
    
    # Try to load existing results to resume if interrupted
    if RESULTS_JSON_PATH.exists():
        try:
            with open(RESULTS_JSON_PATH, "r") as f:
                content = f.read().strip()
                if content:
                     results = json.loads(content)
                     print(f"Loaded {len(results)} existing results. Resuming...")
        except BaseException as e:
             print(f"Could not load existing results. Starting fresh.")
             results = []

    if RETRY_FAILED:
        original_count = len(results)
        results = [r for r in results if r.get("status") == "Success"]
        if len(results) < original_count:
            print(f"Filtered out {original_count - len(results)} failed cases for retry.")

    processed_names = {r.get("name") for r in results}

    for idx, case in enumerate(pairs):
        case_name = case.get("name")
        if case_name in processed_names:
             print(f"[{idx+1}/{len(pairs)}] Skipping '{case_name}' - already processed.")
             continue
             
        original_rel_path = case.get("original")
        prompt_text = case.get("prompt")
        
        print(f"\\n{'='*50}")
        print(f"[{idx+1}/{len(pairs)}] Processing Case: {case_name}")
        print(f"Prompt: {prompt_text[:100]}...")
        
        # Resolve absolute path for the original PPTX
        # Note: The JSON paths are relative to the PPTArena root, 
        # but in standard PPTArena they might be under a 'data' dir or relative to root.
        # Assuming they are relative to the project root where we run this script.
        original_abs_path = Path.cwd() / "data" / original_rel_path
        if not original_abs_path.exists():
             original_abs_path = Path.cwd() / "src" / "data" / original_rel_path
             if not original_abs_path.exists():
                original_abs_path = Path.cwd() / original_rel_path
                
        if not original_abs_path.exists():
            print(f"  ERROR: Original file not found at {original_abs_path}")
            case_result = case.copy()
            case_result["status"] = "Failed"
            case_result["error"] = f"Original file not found: {original_rel_path}"
            results.append(case_result)
            continue
            
        print(f"File: {original_abs_path}")
        
        try:
            request_id = f"bench_{int(time.time())}_{idx}"
            
            # Run the orchestrator
            start_time = time.time()
            orchestrator_result = orchestrator.process_presentation_hybrid(
                original_filepath=str(original_abs_path),
                prompt_text=prompt_text,
                selected_model_id=MODEL_ID,
                use_pre_analysis=True,
                request_id=request_id,
                api_keys=api_keys
            )
            eval_time = time.time() - start_time
            
            # Build the result object
            case_result = case.copy()
            
            if isinstance(orchestrator_result, dict):
                if orchestrator_result.get("error"):
                    print(f"  ERROR: {orchestrator_result['error']}")
                    case_result["status"] = "Failed"
                    case_result["error"] = orchestrator_result["error"]
                else:
                    print(f"  SUCCESS in {eval_time:.1f}s")
                    case_result["status"] = "Success"
                    
                    # Store outputs
                    case_result["llm_engine_used"] = orchestrator_result.get("llm_engine_used", MODEL_ID)
                    case_result["timing_stats"] = orchestrator_result.get("timing_stats", {})
                    
                    if "modified_xml_data" in orchestrator_result:
                         case_result["modified_xml_data"] = orchestrator_result["modified_xml_data"]
                         case_result["llm_response"] = orchestrator_result.get("llm_response", "")
                         
                    if "generated_code" in orchestrator_result:
                         case_result["generated_code"] = orchestrator_result["generated_code"]
                         case_result["generated_content"] = orchestrator_result.get("generated_content", {})
                         
                    # Copy the modified PPTX to the output directory if it exists
                    mod_path = orchestrator_result.get("modified_pptx_filepath")
                    if mod_path and Path(mod_path).exists():
                         dest_path = OUTPUT_DIR / f"modified_case_{idx+1}_{Path(mod_path).name}"
                         try:
                             shutil.copy(mod_path, dest_path)
                             case_result["benchmark_modified_filepath"] = str(dest_path)
                         except Exception as e:
                             print(f"  Warning: failed to copy modified file: {e}")
                             
            else:
                 print(f"  ERROR: Unexpected result format: {type(orchestrator_result)}")
                 case_result["status"] = "Failed"
                 case_result["error"] = f"Unexpected result format: {type(orchestrator_result)}"
                 
        except Exception as e:
            print(f"  FATAL ERROR during processing: {e}")
            case_result = case.copy()
            case_result["status"] = "Failed"
            case_result["error"] = str(e)
            import traceback
            traceback.print_exc()
            
        # Add to results and save incrementally
        results.append(case_result)
        
        with open(RESULTS_JSON_PATH, "w") as f:
            json.dump(results, f, indent=2)
            
        # Generate HTML report incrementally so progress is visible
        generate_html_report(results)

    print(f"\\n{'='*50}")
    print("Benchmark Evaluation Complete!")
    print(f"Total Cases: {len(results)}")
    
    successes = sum(1 for r in results if r.get("status") == "Success")
    failures = len(results) - successes
    print(f"Successes: {successes}")
    print(f"Failures: {failures}")
    
    print(f"Outputs saved to {RESULTS_JSON_PATH} and {REPORT_HTML_PATH}")

if __name__ == "__main__":
    main()
