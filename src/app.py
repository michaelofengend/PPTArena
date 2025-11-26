# app.py
import os
import json
import shutil
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename
import progress
import llm_handler 
import re 
from pathlib import Path 
import time
import csv
from datetime import datetime
import uuid
import sys
import tempfile

# Import from new modules
from ppt import (
    pptx_to_json,
    convert_pptx_to_pdf,
    export_slides_to_images,
    # image_to_base64, # Not directly exported from ppt/__init__.py, need to check
    extract_specific_xml_from_pptx
)

def image_to_base64(image_path):
    """Converts an image file to a base64 encoded string."""
    try:
        import base64
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error converting image {image_path} to base64: {e}")
        return None

# --- Environment Detection ---
IS_GUNICORN = "gunicorn" in sys.modules

app = Flask(__name__)
CORS(app)

# --- Disable Caching for Development ---
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# --- MODIFIED: Configuration ---
# UPLOAD_FOLDER is removed, as we now reference the benchmark ppts directly
SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "work_dir"
USER_UPLOADS_FOLDER = DATA_DIR / "user_uploads"
SESSIONS_FOLDER = DATA_DIR / "sessions"
TSBENCH_PRESENTATIONS_DIR = SCRIPT_DIR / "TSBench" / "benchmark_ppts"
EXTRACTED_XML_FOLDER = DATA_DIR / 'extracted_xml_original'
MODIFIED_PPTX_FOLDER = DATA_DIR / 'modified_ppts'
GENERATED_IMAGES_FOLDER = DATA_DIR / 'generated_images'
GENERATED_PDFS_FOLDER = DATA_DIR / 'generated_pdfs'
PROCESSING_LOG_CSV = SCRIPT_DIR / 'processing_log.csv'
EVALUATION_RESULTS_CSV = SCRIPT_DIR / 'evaluation_results.csv'


ALLOWED_EXTENSIONS = {'pptx'}

# --- MODIFIED: Use Path objects for consistency ---
app.config['EXTRACTED_XML_FOLDER'] = str(EXTRACTED_XML_FOLDER)
app.config['MODIFIED_PPTX_FOLDER'] = str(MODIFIED_PPTX_FOLDER)
app.config['GENERATED_IMAGES_FOLDER'] = str(GENERATED_IMAGES_FOLDER)
app.config['GENERATED_PDFS_FOLDER'] = str(GENERATED_PDFS_FOLDER)
app.config['TSBENCH_PRESENTATIONS_DIR'] = str(TSBENCH_PRESENTATIONS_DIR)
app.config['USER_UPLOADS_FOLDER'] = str(USER_UPLOADS_FOLDER)
app.config['SESSIONS_FOLDER'] = str(SESSIONS_FOLDER)

# --- MODIFIED: Create only necessary directories ---
for folder in [DATA_DIR, EXTRACTED_XML_FOLDER, MODIFIED_PPTX_FOLDER, GENERATED_IMAGES_FOLDER, GENERATED_PDFS_FOLDER, USER_UPLOADS_FOLDER, SESSIONS_FOLDER]:
    folder.mkdir(parents=True, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_pdf_preview_url(pptx_path):
    """Convert pptx to PDF and return the relative /view_pdf URL if available."""
    try:
        pdf_path = convert_pptx_to_pdf(str(pptx_path), app.config['GENERATED_PDFS_FOLDER'])
        if pdf_path:
            return f"/view_pdf/{Path(pdf_path).name}"
    except Exception as e:
        app.logger.error(f"Failed to generate PDF preview for {pptx_path}: {e}", exc_info=True)
    return None

def log_processing_details(log_data):
    """Appends a record to the processing log CSV file."""
    file_exists = os.path.isfile(PROCESSING_LOG_CSV)
    with open(PROCESSING_LOG_CSV, 'a', newline='') as csvfile:
        fieldnames = [
            'Timestamp', 'OriginalFilename', 'LLMEngineUsed', 
            'TotalProcessingTimeSeconds', 'JSONExtractionTimeSeconds', 
            'XMLExtractionTimeSeconds', 'LLMInferenceTimeSeconds', 
            'PPTXModificationTimeSeconds', 'ImageConversionTimeSeconds',
            'TotalSlidesInOriginal', 'NumberOfSlidesEditedByLLM', 
            'ModifiedXMLFilesList'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(log_data)

def _get_slide_number_from_path(filepath):
    """Extracts the slide number from an image path like '.../slide-12.png'."""
    # The last number in the filename is assumed to be the slide number.
    # e.g., 'slide-0001-5.png' -> 5
    numbers = re.findall(r'\d+', Path(filepath).name)
    if numbers:
        return int(numbers[-1])
    return 0

@app.route('/')
def index():
    """Redirect to the evaluation page by default."""
    return redirect(url_for('evaluation_page'))

def build_evaluation_context(selected_pair_name=None, prediction_pptx_path=None):
    """Shared builder for evaluation/chat tabs so both routes render the same page."""
    evaluation_pairs = get_evaluation_pairs()
    selected_pair = next(
        (p for p in evaluation_pairs if p['name'] == selected_pair_name),
        evaluation_pairs[0] if evaluation_pairs else None
    )

    if not selected_pair:
        return None, "Error: evaluation_pairs_refined.json is missing or empty."

    gt_ppt_path = SCRIPT_DIR.parent / selected_pair['ground_truth']
    gt_pdf = convert_pptx_to_pdf(str(gt_ppt_path), app.config['GENERATED_PDFS_FOLDER'])

    pred_pdf = None
    pred_ppt = None
    is_prediction = False
    if prediction_pptx_path and Path(prediction_pptx_path).exists():
        pred_ppt = Path(prediction_pptx_path)
        pred_pdf = convert_pptx_to_pdf(str(pred_ppt), app.config['GENERATED_PDFS_FOLDER'])
        is_prediction = True

    if not pred_pdf:
        initial_pred_ppt = SCRIPT_DIR.parent / selected_pair['original']
        if initial_pred_ppt.exists():
            pred_ppt = initial_pred_ppt
            pred_pdf = convert_pptx_to_pdf(str(pred_ppt), app.config['GENERATED_PDFS_FOLDER'])

    gt_pdf_name = Path(gt_pdf).name if gt_pdf else None
    pred_pdf_name = Path(pred_pdf).name if pred_pdf else None
    prediction_pptx_name = pred_ppt.name if pred_ppt else None

    context = dict(
        evaluation_pairs=evaluation_pairs,
        selected_pair=selected_pair,
        test_pdf_name=gt_pdf_name,
        prediction_pdf_name=pred_pdf_name,
        prediction_pptx_name=prediction_pptx_name,
        is_prediction=is_prediction
    )
    return context, None

@app.route('/app')
def app_page():
    """Chat/editor entry point. Renders the shared tabbed page with Chat as default."""
    selected_pair_name = request.args.get('pair')
    prediction_path = request.args.get('prediction_pptx_path')
    active_tab = request.args.get('tab') or 'chat'
    context, error = build_evaluation_context(selected_pair_name, prediction_path)
    if error:
        return error, 500
    return render_template('evaluation.html', active_tab=active_tab, **context)


def get_evaluation_pairs():
    """Reads and returns the evaluation pairs from the JSON file."""
    try:
        with open(SCRIPT_DIR.parent / 'evaluation_pairs_refined.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

@app.route('/evaluation', methods=['GET'])
def evaluation_page():
    """Render comparison/judge tab (same HTML as chat)."""
    selected_pair_name = request.args.get('pair')
    prediction_path = request.args.get('prediction_pptx_path')
    active_tab = request.args.get('tab') or 'evaluation'
    context, error = build_evaluation_context(selected_pair_name, prediction_path)
    if error:
        return error, 500
    return render_template('evaluation.html', active_tab=active_tab, **context)

@app.route('/download_original/<session_id>/<filename>')
def download_original_file(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=True)

@app.route('/download_modified/<session_id>/<filename>')
def download_modified_file(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=True)

@app.route('/preview_ppt/original/<session_id>/<filename>')
def preview_original_ppt(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=False)

@app.route('/preview_ppt/original/<filename>')
def preview_original_upload(filename):
    """Serve a temporarily uploaded PPTX before a session is created."""
    return send_from_directory(app.config['USER_UPLOADS_FOLDER'], filename, as_attachment=False)

@app.route('/preview_ppt/modified/<session_id>/<filename>')
def preview_modified_ppt(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=False)

@app.route('/view_slide_image/<path:image_path>')
def view_slide_image(image_path):
    """Serves an image from the generated_images directory."""
    return send_from_directory(app.config['GENERATED_IMAGES_FOLDER'], image_path, as_attachment=False)

@app.route('/view_pdf/<path:pdf_path>')
def view_pdf(pdf_path):
    """Serve a PDF from the generated_pdfs directory."""
    return send_from_directory(app.config['GENERATED_PDFS_FOLDER'], pdf_path, as_attachment=False)

@app.route('/public/preview/<path:filename>')
def public_preview(filename):
    """Serve PPTX files publicly for Microsoft Live viewer without requiring cookies."""
    # For now, serve from user uploads - in production, this should be a signed URL to S3/GCS
    try:
        response = send_from_directory(
            app.config['USER_UPLOADS_FOLDER'], 
            filename, 
            as_attachment=False,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation'
        )
        # Set proper headers for Microsoft Live viewer
        response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        response.headers['Cache-Control'] = 'private, max-age=0'
        return response
    except FileNotFoundError:
        return "File not found", 404


@app.route('/files/<path:filepath>')
def serve_file_in_root(filepath):
    """Serve files from the root directory for evaluation purposes."""
    return send_from_directory(SCRIPT_DIR.parent, filepath)

@app.route('/process_eval_prediction', methods=['POST'])
def process_eval_prediction():
    """Process a PPTX on the evaluation page to produce a prediction."""
    import orchestrator
    generation_start_time = time.time()
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part in request.'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected.'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed.'}), 400

        prompt_text = request.form.get('prompt', '')
        selected_model_id = request.form.get('llm_engine')
        use_pre_analysis = request.form.get('use_pre_analysis', 'on') == 'on'
        api_keys = {
            "openai": request.form.get('openai_api_key'),
            "gemini": request.form.get('gemini_api_key')
        }
        force_python_pptx = request.form.get('force_python_pptx') == 'on'
        loop_mode = request.form.get('loop_mode') == 'on'
        loop_iterations_raw = request.form.get('loop_iterations')
        try:
            loop_iterations = int(loop_iterations_raw) if loop_iterations_raw else 1
        except ValueError:
            loop_iterations = 1
        loop_iterations = max(1, loop_iterations)

        original_filename_secure = secure_filename(file.filename)
        temp_filename = f"eval_{uuid.uuid4().hex}_{original_filename_secure}"
        original_filepath = USER_UPLOADS_FOLDER / temp_filename
        file.save(original_filepath)

        # Prefer client-provided request id for continuity if present
        client_request_id = request.form.get('client_request_id')
        request_id = client_request_id or f"eval-{uuid.uuid4().hex}"
        progress.start(request_id)
        progress.append(request_id, "Started processing evaluation request")

        processing_result = orchestrator.process_presentation_hybrid(
            original_filepath=str(original_filepath),
            prompt_text=prompt_text,
            selected_model_id=selected_model_id,
            use_pre_analysis=use_pre_analysis,
            request_id=request_id,
            api_keys=api_keys,
            force_python_pptx=force_python_pptx,
            loop_mode=loop_mode,
            loop_max_iterations=loop_iterations,
        )

        planning_plan = processing_result.get('planning_plan') if isinstance(processing_result, dict) else None
        planning_model = processing_result.get('planning_model') if isinstance(processing_result, dict) else None
        if isinstance(planning_plan, dict):
            target_count = len(planning_plan.get('targets') or [])
            progress.append(
                request_id,
                f"Planning via {planning_model or 'gpt-5-nano'} selected {target_count} target file(s)."
            )

        if processing_result.get('error'):
            generation_time = round(time.time() - generation_start_time, 2)
            processing_result['generation_time_seconds'] = generation_time
            return jsonify(processing_result), 500

        modified_pptx_path = processing_result.get('modified_pptx_filepath')
        pred_pdf = convert_pptx_to_pdf(modified_pptx_path, GENERATED_PDFS_FOLDER) if modified_pptx_path else None
        progress.append(request_id, "Converted prediction to PDF")

        generation_time = round(time.time() - generation_start_time, 2)
        progress.append(request_id, f"Finished processing (took {generation_time}s)")
        return jsonify({
            'prediction_pdf_name': Path(pred_pdf).name if pred_pdf else None,
            'prediction_pptx_name': Path(modified_pptx_path).name if modified_pptx_path else None,
            'modified_pptx_filepath': str(modified_pptx_path) if modified_pptx_path else None,
            'message': 'Processing successful!',
            'request_id': request_id,
            'generation_time_seconds': generation_time,
            'loop_mode_enabled': processing_result.get('loop_mode_enabled', False),
            'loop_iterations_requested': processing_result.get('loop_iterations_requested'),
            'loop_iterations_completed': processing_result.get('loop_iterations_completed'),
            'loop_iteration_summaries': processing_result.get('loop_iteration_summaries'),
            'planning_plan': processing_result.get('planning_plan'),
            'planning_model': processing_result.get('planning_model'),
        })
    except AttributeError as e:
        # Explicitly catch the circular import error and return it as JSON
        error_message = {
            "error": "Circular Import Error Detected on Server",
            "details": f"The server crashed with an 'AttributeError: {e}'. This is a classic symptom of a circular import loop (e.g., app.py -> orchestrator.py -> some_other_module.py -> app.py).",
            "solution": "To fix this, the 'import orchestrator' statement must be moved inside the function that uses it, instead of being at the top of the file. Please accept the next change to apply the permanent fix."
        }
        app.logger.error(f"Circular Import Suspected: {e}", exc_info=True)
        return jsonify(error_message), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred in process_eval_prediction: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected server error occurred: {str(e)}"}), 500


@app.route('/upload_only', methods=['POST'])
def upload_only_route():
    """Handles file upload for quick preview without processing."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        original_filename_secure = secure_filename(file.filename)
        unique_id = uuid.uuid4().hex[:8]
        save_filename = f"{unique_id}_{original_filename_secure}"
        saved_filepath = os.path.join(app.config['USER_UPLOADS_FOLDER'], save_filename)
        file.save(saved_filepath)
        preview_url = f"/preview_ppt/original/{save_filename}"
        # Generate public preview URL for Microsoft Live viewer
        public_preview_url = f"/public/preview/{save_filename}"
        pdf_preview_url = generate_pdf_preview_url(saved_filepath)
        return jsonify({
            'preview_url': preview_url,
            'public_preview_url': public_preview_url,
            'pdf_preview_url': pdf_preview_url
        }), 200
    else:
        return jsonify({'error': 'File type not allowed'}), 400

@app.route('/judge', methods=['POST'])
def judge_edit_route():
    """
    On-demand endpoint to call the LLM judge for a specific slide edit.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        user_prompt = data.get('instruction') # Keep 'instruction' from frontend for now
        original_slide_image_b64 = data.get('original_slide_image_b64')
        modified_slide_image_b64 = data.get('modified_slide_image_b64')
        original_slide_xml = data.get('original_slide_xml')
        modified_slide_xml = data.get('modified_slide_xml')
        judge_model = data.get('model_id', 'gemini-3-pro-preview') # Default to a powerful model
        api_keys = {
            "openai": data.get('openai_api_key'),
            "gemini": data.get('gemini_api_key')
        }
        request_id = data.get('request_id', 'judging')

        # Basic validation
        if not all([user_prompt, original_slide_image_b64, modified_slide_image_b64, original_slide_xml, modified_slide_xml]):
            return jsonify({"error": "Missing required fields for judging"}), 400
        
        # --- Call Judge ---
        judge_result = llm_handler.call_llm_judge(
            user_prompt=user_prompt,
            original_slide_image_b64=original_slide_image_b64,
            modified_slide_image_b64=modified_slide_image_b64,
            original_slide_xml=original_slide_xml,
            modified_slide_xml=modified_slide_xml,
            judge_model=judge_model,
            request_id=request_id,
            api_keys=api_keys
        )

        return jsonify(judge_result)

    except Exception as e:
        app.logger.error(f"Error during judging: {e}", exc_info=True)
        return jsonify({"error": f"An error occurred during judging: {str(e)}"}), 500

@app.route('/process_ppt', methods=['POST'])
def process_ppt_route():
    """
    Main endpoint for processing PPTX files with user prompts.
    Creates a new session and processes the presentation.
    """
    import orchestrator
    start_time = time.time()
    data = request.form
    
    # Validate required fields
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed. Only .pptx files are supported.'}), 400
    
    prompt_text = data.get('prompt', '')
    selected_model_id = data.get('llm_engine')
    use_pre_analysis = data.get('use_pre_analysis', 'on') == 'on'
    api_keys = {
        "openai": data.get('openai_api_key'),
        "gemini": data.get('gemini_api_key')
    }
    force_python_pptx = data.get('force_python_pptx') == 'on'
    
    if not all([prompt_text, selected_model_id]):
        return jsonify({'error': 'Missing required fields: prompt and llm_engine'}), 400
    
    # Create session directory
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    session_path.mkdir(parents=True, exist_ok=True)
    
    # Save uploaded file
    original_filename_secure = secure_filename(file.filename)
    original_filepath = session_path / 'original.pptx'
    file.save(original_filepath)
    
    request_id = f"{session_id}-{int(time.time())}"
    progress.start(request_id)
    progress.append(request_id, "Started processing presentation")
    
    # Process the presentation
    processing_result = orchestrator.process_presentation_hybrid(
        original_filepath=str(original_filepath),
        prompt_text=prompt_text,
        selected_model_id=selected_model_id,
        use_pre_analysis=use_pre_analysis,
        request_id=request_id,
        api_keys=api_keys,
        session_id=session_id,
        force_python_pptx=force_python_pptx
    )
    
    if processing_result.get("error"):
        return jsonify(processing_result), 500
    
    # Set up URLs for the response
    processing_result["session_id"] = session_id
    processing_result["original_pptx_download_url"] = f"/download_original/{session_id}/original.pptx"
    processing_result["original_pptx_url"] = f"/preview_ppt/original/{session_id}/original.pptx"
    
    if processing_result.get("modified_pptx_filepath"):
        modified_path = Path(processing_result["modified_pptx_filepath"])
        if modified_path.exists():
            # Copy to session directory only if it's a different file
            session_modified_path = session_path / 'modified.pptx'
            try:
                if modified_path.resolve() != session_modified_path.resolve():
                    shutil.copy(modified_path, session_modified_path)
            except shutil.SameFileError:
                # File is already in the right place, no need to copy
                pass
            
            processing_result["modified_pptx_download_url"] = f"/download_modified/{session_id}/modified.pptx"
            processing_result["modified_pptx_url"] = f"/preview_ppt/modified/{session_id}/modified.pptx"
            # Generate public preview URL for Microsoft Live viewer
            processing_result["public_preview_url"] = f"/public/preview/{session_modified_path.name}"
            pdf_url = generate_pdf_preview_url(session_modified_path)
            if pdf_url:
                processing_result["pdf_preview_url"] = pdf_url
    
    progress.append(request_id, "Finished processing")
    processing_result["request_id"] = request_id
    return jsonify(processing_result)

@app.route('/save_evaluation_result', methods=['POST'])
def save_evaluation_result():
    """Save evaluation results to CSV file."""
    try:
        data = request.get_json(silent=True) or {}
        
        # Extract data from request
        pair_name = data.get('pair_name', '')
        llm_engine = data.get('llm_engine', '')
        generation_time = data.get('generation_time_seconds', '')
        judge_time = data.get('judge_time_seconds', '')
        instruction_following_score = data.get('instruction_following_score', '')
        visual_quality_score = data.get('visual_quality_score', '')
        instruction_following_reason = data.get('instruction_following_reason', '')
        visual_quality_reason = data.get('visual_quality_reason', '')
        judge_model = data.get('judge_model', '')
        
        # Check if CSV exists to determine if we need to write header
        file_exists = EVALUATION_RESULTS_CSV.exists()
        
        # Prepare row data
        row = {
            'timestamp': datetime.now().isoformat(),
            'pair_name': pair_name,
            'llm_engine': llm_engine,
            'generation_time_seconds': generation_time,
            'judge_model': judge_model,
            'judge_time_seconds': judge_time,
            'instruction_following_score': instruction_following_score,
            'visual_quality_score': visual_quality_score,
            'instruction_following_reason': instruction_following_reason,
            'visual_quality_reason': visual_quality_reason
        }
        
        # Append to CSV
        with open(EVALUATION_RESULTS_CSV, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'timestamp', 'pair_name', 'llm_engine', 'generation_time_seconds',
                'judge_model', 'judge_time_seconds', 'instruction_following_score',
                'visual_quality_score', 'instruction_following_reason', 
                'visual_quality_reason'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
            
            writer.writerow(row)
        
        return jsonify({'success': True, 'message': 'Results saved to CSV'})
    except Exception as e:
        app.logger.error(f"Error saving evaluation result: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/judge_arena', methods=['POST'])
def judge_arena_route():
    """Judge ground truth vs prediction using arena prompt."""
    judge_start_time = time.time()
    try:
        data = request.get_json(silent=True) or {}
        selected_pair_name = data.get('pair_name')
        prediction_name = data.get('prediction_pptx_name')
        judge_model = data.get('judge_model') or 'gemini-3-pro-preview'

        evaluation_pairs = get_evaluation_pairs()
        selected_pair = next((p for p in evaluation_pairs if p['name'] == selected_pair_name), None)

        if not selected_pair:
            return jsonify({'error': f"Evaluation pair '{selected_pair_name}' not found."}), 404

        # --- Define the three PPTX paths ---
        initial_ppt_path = SCRIPT_DIR.parent / selected_pair['original']
        gt_ppt_path = SCRIPT_DIR.parent / selected_pair['ground_truth']
        pred_ppt = None
        
        if prediction_name:
            # Check for the prediction file in all relevant folders
            possible_paths = [
                MODIFIED_PPTX_FOLDER / prediction_name,
                SCRIPT_DIR.parent / prediction_name,
                USER_UPLOADS_FOLDER / prediction_name
            ]
            for path in possible_paths:
                if path.exists():
                    pred_ppt = path
                    break
            if not pred_ppt:
                 return jsonify({'error': f"Prediction file '{prediction_name}' not found."}), 404
        else:
            # Fallback to the initial file if no prediction has been made
            pred_ppt = initial_ppt_path

        if not pred_ppt or not pred_ppt.exists():
             return jsonify({'error': 'A valid prediction presentation file could not be found.'}), 404

        # --- Generate full JSON summaries for all three presentations ---
        initial_json = pptx_to_json(str(initial_ppt_path))
        gt_json = pptx_to_json(str(gt_ppt_path))
        pred_json = pptx_to_json(str(pred_ppt))

        with tempfile.TemporaryDirectory() as tmpdir:
            init_dir = Path(tmpdir) / "initial"
            gt_dir = Path(tmpdir) / "ground_truth"
            pred_dir = Path(tmpdir) / "prediction"
            init_imgs = export_slides_to_images(str(initial_ppt_path), str(init_dir))
            gt_imgs = export_slides_to_images(str(gt_ppt_path), str(gt_dir))
            pred_imgs = export_slides_to_images(str(pred_ppt), str(pred_dir))
            init_b64_all = [image_to_base64(p) for p in init_imgs]
            gt_b64_all = [image_to_base64(p) for p in gt_imgs]
            pred_b64_all = [image_to_base64(p) for p in pred_imgs]

        gt_xml = extract_specific_xml_from_pptx(str(gt_ppt_path), 'ppt/slides/slide1.xml') or ''
        pred_xml = extract_specific_xml_from_pptx(str(pred_ppt), 'ppt/slides/slide1.xml') or ''

        judge_result = llm_handler.call_llm_judge(
            user_prompt=f"Instruction: {selected_pair['prompt']}\nStyle Target: {selected_pair['style_target']}",
            initial_ppt_json=initial_json,
            original_ppt_json=gt_json,
            modified_ppt_json=pred_json,
            initial_slide_images_b64=init_b64_all,
            original_slide_images_b64=gt_b64_all,
            modified_slide_images_b64=pred_b64_all,
            original_slide_xml=gt_xml,
            modified_slide_xml=pred_xml,
            judge_model=judge_model,
            evaluation_mode='arena',
            api_keys={
                "openai": data.get('openai_api_key'),
                "gemini": data.get('gemini_api_key')
            }
        )

        if not judge_result:
             return jsonify({'error': 'Judge returned no result.'}), 500
             
        if judge_result.get('error'):
             return jsonify(judge_result), 500

        # Ensure frontend receives expected keys
        judge_result.setdefault('instruction_following_score', 'N/A')
        judge_result.setdefault('visual_quality_score', 'N/A')
        judge_result.setdefault('instruction_following_reason', '')
        judge_result.setdefault('visual_quality_reason', '')
        
        judge_time = round(time.time() - judge_start_time, 2)
        judge_result['judge_time_seconds'] = judge_time
        
        return jsonify(judge_result)
    except Exception as e:
        app.logger.error(f"Error during arena judging: {e}", exc_info=True)
        judge_time = round(time.time() - judge_start_time, 2)
        return jsonify({'error': str(e), 'judge_time_seconds': judge_time}), 500

@app.route('/api/edit', methods=['POST'])
def edit_existing_ppt_route():
    """
    Stateful endpoint for continued editing of a presentation.
    """
    import orchestrator
    start_time = time.time()
    data = request.form
    session_id = data.get('session_id')
    prompt_text = data.get('prompt')
    selected_model_id = data.get('llm_engine')
    use_pre_analysis = data.get('use_pre_analysis', 'on') == 'on'
    api_keys = {
        "openai": data.get('openai_api_key'),
        "gemini": data.get('gemini_api_key')
    }
    force_python_pptx = data.get('force_python_pptx') == 'on'
    
    if not all([session_id, prompt_text, selected_model_id]):
        return jsonify({'error': 'Missing session_id, prompt, or llm_engine'}), 400

    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    if not session_path.exists():
        return jsonify({'error': 'Session not found'}), 404

    # The 'current' version to be edited is the last modified one.
    current_ppt_path = session_path / 'modified.pptx'
    if not current_ppt_path.exists():
        return jsonify({'error': 'No modifiable presentation found in session'}), 404
        
    request_id = f"{session_id}-{int(time.time())}"
    progress.start(request_id)
    progress.append(request_id, "Started editing session presentation")
    
    # Process the presentation. This function is now the core logic.
    processing_result = orchestrator.process_presentation_hybrid(
        original_filepath=str(current_ppt_path),
        prompt_text=prompt_text,
        selected_model_id=selected_model_id,
        use_pre_analysis=use_pre_analysis,
        request_id=request_id,
        api_keys=api_keys,
        session_id=session_id, # Pass session_id to logic
        force_python_pptx=force_python_pptx
    )

    if processing_result.get("error"):
        return jsonify(processing_result), 500

    # Overwrite the 'modified.pptx' with the new version while keeping history
    if processing_result.get("modified_pptx_filepath"):
        newly_modified_path = Path(processing_result["modified_pptx_filepath"])
        if newly_modified_path.exists():
            # Save the previous version before overwriting
            history_files = sorted(session_path.glob("history_*.pptx"))
            next_idx = len(history_files) + 1
            history_path = session_path / f"history_{next_idx}.pptx"
            if current_ppt_path.exists() and current_ppt_path.resolve() != history_path.resolve():
                shutil.copy(current_ppt_path, history_path)

            # Replace the current modifiable file if different
            if newly_modified_path.resolve() != current_ppt_path.resolve():
                shutil.copy(newly_modified_path, current_ppt_path)


            # Update URLs for frontend
            processing_result["modified_pptx_download_url"] = f"/download_modified/{session_id}/modified.pptx"
            processing_result["modified_pptx_url"] = f"/preview_ppt/modified/{session_id}/modified.pptx"
            processing_result["original_pptx_download_url"] = f"/download_original/{session_id}/{history_path.name}"
            processing_result["original_pptx_url"] = f"/preview_ppt/original/{session_id}/{history_path.name}"
            # Generate public preview URL for Microsoft Live viewer
            processing_result["public_preview_url"] = f"/public/preview/{current_ppt_path.name}"
            pdf_url = generate_pdf_preview_url(current_ppt_path)
            if pdf_url:
                processing_result["pdf_preview_url"] = pdf_url


    progress.append(request_id, "Finished processing")
    processing_result["request_id"] = request_id
    return jsonify(processing_result)

@app.route('/progress', methods=['GET'])
def get_progress():
    request_id = request.args.get('request_id', '')
    since = int(request.args.get('since', '0') or '0')
    messages = progress.get(request_id, since)
    return jsonify({
        'request_id': request_id,
        'since': since,
        'messages': messages,
        'next_index': since + len(messages)
    })


if __name__ == '__main__':
    # Note: The benchmark runner expects the host to be 127.0.0.1 and port 5001
    app.run(host='127.0.0.1', port=5001, debug=True)
