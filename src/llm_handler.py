import json
import os
import openai
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import re
import time
from pathlib import Path
import base64
from typing import Optional, List
from PIL import Image


# Import from new modules
from llm.utils import (
    load_api_keys,
    _log,
    _create_openai_client,
    _is_openai_model,
    _extract_text_from_openai_response,
    extract_json_from_llm_response,
    _configure_gemini_client,
    _build_gemini_generation_config,
    _log_token_info,
    _is_insufficient_quota_error
)
from llm.prompt import (
    _construct_llm_input_prompt,
    build_planning_prompt,
    get_relevant_xml_files_heuristic
)
from llm.judge import call_llm_judge
from utils.image_utils import (
    _pil_from_b64,
    _resize_for_metric,
    _to_gray_np,
    _compute_ssim,
    _dct_8x8_hash,
    _hamming_distance64
)

# Configuration
CREDENTIALS_FILE = "credentials.env"

def call_openai_api(
    user_prompt,
    ppt_json_data,
    xml_file_paths,
    model_id="gpt-5.1-2025-11-13",
    image_inputs=None,
    request_id=None,
    api_key=None,
    edit_history=None,
    edit_plan=None,
):
    # Prefer caller-provided key, fall back to credentials.env/environment
    if api_key:
        resolved_api_key = api_key
        key_source = "frontend"
    else:
        keys = load_api_keys()
        resolved_api_key = keys.get("openai") or keys.get("openai_api_key")
        key_source = "credentials.env/env"
    _log(f"Using OPENAI_API_KEY from {key_source} for OpenAI call", request_id)
    response_data = {"text_response": "", "model_used": model_id, "inference_time_seconds": None}

    if not resolved_api_key:
        response_data["text_response"] = f"Error: OpenAI API key not provided (set in UI or {CREDENTIALS_FILE})."
        return response_data

    try:
        client = _create_openai_client(resolved_api_key)
        if client is None:
            response_data["text_response"] = "Error: Failed to initialize OpenAI client (missing API key?)."
            return response_data

        # Build the common text prompt
        num_slides_with_images = len(image_inputs) if image_inputs else 0
        text_prompt_content = _construct_llm_input_prompt(
            user_prompt,
            ppt_json_data,
            xml_file_paths,
            bool(image_inputs),
            num_slides_with_images=num_slides_with_images,
            edit_history=edit_history,
            request_id=request_id,
            edit_plan=edit_plan,
        )
        # Token count (OpenAI) - delegated to _log_token_info
        _log_token_info(model_id, text_prompt_content, -1, log_type="openai", request_id=request_id)
        # Soft token limit hint to logs for troubleshooting
        try:
            approx_chars = len(text_prompt_content)
            _log(f"Approx prompt chars: {approx_chars}", request_id)
        except Exception:
            pass

        # If GPT-5 family, use the Responses API and explicitly embed base64 images
        if "gpt-5" in model_id.lower():
            _log(f"Calling OpenAI Responses API ({model_id})", request_id)
            content_items = [{"type": "input_text", "text": text_prompt_content}]
            if image_inputs:
                for img_data in image_inputs:
                    try:
                        # Handle both dict with path and direct base64 if needed, assuming dict from provided code
                        if "path" in img_data:
                            with open(img_data["path"], "rb") as f_img:
                                encoded = base64.b64encode(f_img.read()).decode("utf-8")
                            mime = img_data.get("mime_type", "image/png")
                        else:
                            # Fallback if img_data is different structure
                            encoded = "" 
                            mime = "image/png"
                        
                        if encoded:
                            data_url = f"data:{mime};base64,{encoded}"
                            content_items.append({
                                "type": "input_image",
                                "image_url": data_url,
                            })
                    except Exception as e_img:
                        _log(f"Error base64-encoding image {img_data.get('path')}: {e_img}", request_id)
            llm_start_time = time.time()
            resp = client.responses.create(
                model=model_id,
                input=[{"role": "user", "content": content_items}],
            )
            llm_end_time = time.time()
            response_data["inference_time_seconds"] = round(llm_end_time - llm_start_time, 3)
            # Robustly extract text from Responses API
            text_out = getattr(resp, "output_text", None)
            if not text_out:
                try:
                    # New SDKs often expose a .output list → each has .content list items with .text
                    parts = []
                    output_items = getattr(resp, "output", []) or []
                    for out in output_items:
                        for c in getattr(out, "content", []) or []:
                            t = getattr(c, "text", None)
                            if t:
                                parts.append(str(t))
                    text_out = "\n".join(parts) if parts else None
                except Exception:
                    text_out = None
            if not text_out:
                try:
                    # Fallback to dict conversion if available
                    if hasattr(resp, "to_dict"):
                        text_out = json.dumps(resp.to_dict())
                    else:
                        text_out = str(resp)
                except Exception:
                    text_out = str(resp)
            response_data["text_response"] = text_out
            # Log a brief summary of the output for debugging
            _log(f"OpenAI Responses output length: {len(text_out or '')}", request_id)
            if not text_out:
                try:
                    _log(f"OpenAI Responses raw (truncated): {str(resp)[:300]}...", request_id)
                except Exception:
                    pass
            _log(f"OpenAI Responses API call successful (took {response_data['inference_time_seconds']:.3f}s)", request_id)
        else:
            # Chat Completions path (GPT-4/4o family etc.)
            message_content_parts = [{"type": "text", "text": text_prompt_content}]
            if image_inputs and model_id in ["gpt-4o", "gpt-4-turbo", "gpt-4-vision-preview"]:
                _log(f"Preparing {len(image_inputs)} image(s) for OpenAI API ({model_id})", request_id)
                for img_data in image_inputs:
                    try:
                        with open(img_data["path"], "rb") as image_file:
                            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                        data_url = f"data:{img_data['mime_type']};base64,{encoded_string}"
                        message_content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "low"}
                        })
                    except Exception as e_img:
                        _log(f"Error processing image {img_data['path']} for OpenAI: {e_img}", request_id)
                        message_content_parts.append({"type": "text", "text": f"[Error processing image: {Path(img_data['path']).name}]"})
            elif image_inputs:
                _log(f"Warning: Images provided but model {model_id} may not be vision-capable for OpenAI. Sending text only.", request_id)

            payload_content = message_content_parts if (image_inputs and model_id in ["gpt-4o", "gpt-4-turbo", "gpt-4-vision-preview"]) else text_prompt_content
            _log(f"Calling OpenAI Chat Completions ({model_id}) (multimodal: {bool(image_inputs and 'gpt-4' in model_id)})", request_id)
            llm_start_time = time.time()
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": payload_content}],
                model=model_id,
            )
            llm_end_time = time.time()
            response_data["inference_time_seconds"] = round(llm_end_time - llm_start_time, 3)
            response_data["text_response"] = chat_completion.choices[0].message.content
            _log(f"OpenAI Chat call successful (took {response_data['inference_time_seconds']:.3f}s, output length {len(response_data['text_response'] or '')})", request_id)
    except openai.APIConnectionError as e:
        _log(f"OpenAI API Connection Error: {e}", request_id)
        response_data["text_response"] = f"OpenAI API Connection Error: {e}"
    except openai.RateLimitError as e:
        _log(f"OpenAI API Rate Limit Error: {e}", request_id)
        response_data["text_response"] = f"OpenAI API Rate Limit Error: {e}"
    except openai.AuthenticationError as e:
        _log(f"OpenAI API Authentication Error: {e}", request_id)
        response_data["text_response"] = f"OpenAI API Authentication Error: {e} (Check your API key)"
    except openai.BadRequestError as e: 
        _log(f"OpenAI API BadRequestError: {e}", request_id)
        response_data["text_response"] = f"OpenAI API BadRequestError: {e}. The prompt or image data might be too long or invalid."
    except openai.APIError as e: 
        _log(f"OpenAI API Error: {e}", request_id)
        response_data["text_response"] = f"OpenAI API Error: {e}"
    except Exception as e: 
        _log(f"Unexpected OpenAI error: {e}", request_id)
        response_data["text_response"] = f"An unexpected error occurred with OpenAI API: {e}"
    return response_data


def call_gemini_api(
    user_prompt,
    ppt_json_data,
    xml_file_paths,
    model_id="gemini-3-pro-preview",
    image_inputs=None,
    request_id=None,
    api_key=None,
    edit_history=None,
    edit_plan=None,
):
    if not api_key:
        keys = load_api_keys()
        api_key = keys.get("gemini") or keys.get("gemini_api_key")
    response_data = {"text_response": "", "model_used": model_id, "inference_time_seconds": None}

    if not api_key:
        response_data["text_response"] = f"Error: Gemini API key not found in {CREDENTIALS_FILE}"
        return response_data

    try:
        _configure_gemini_client(model_id, api_key)
        
        # --- MODIFIED: Stricter safety settings ---
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

        # --- MODIFIED: Add request options for timeout ---
        request_options = {"timeout": 600} # 10 minutes

        # Configure generation defaults (thinking level, media resolution for images, etc.)
        generation_config = _build_gemini_generation_config(
            model_id,
            use_high_res_media=bool(image_inputs),
        )
        model_kwargs = {"model_name": model_id, "safety_settings": safety_settings}
        if generation_config:
            model_kwargs["generation_config"] = generation_config
        model = genai.GenerativeModel(**model_kwargs)

        prompt_parts_for_api = []
        num_slides_with_images = len(image_inputs) if image_inputs else 0
        
        text_prompt_content = _construct_llm_input_prompt(
            user_prompt,
            ppt_json_data,
            xml_file_paths,
            bool(image_inputs),
            num_slides_with_images=num_slides_with_images,
            edit_history=edit_history,
            request_id=request_id,
            edit_plan=edit_plan,
        )
        # Token count (Gemini)
        _log_token_info(model_id, text_prompt_content, -1, log_type="gemini_approx", request_id=request_id)
        
        # --- Vision Model Handling ---
        if 'vision' in model_id or 'pro' in model_id or 'o' in model_id or 'flash' in model_id:
            _log(f"Calling Gemini API ({model_id}) (with images)", request_id)
            
            prompt_parts_for_api.append(text_prompt_content)
            if image_inputs:
                _log(f"Preparing {len(image_inputs)} image(s) for Gemini API ({model_id})", request_id)
                for img_data in image_inputs:
                    try:
                        _log(f"  - Processing image for slide: {Path(img_data['path']).name}", request_id)
                        img = Image.open(img_data['path'])
                        prompt_parts_for_api.append(img)
                    except Exception as e_img:
                        _log(f"Error processing image {img_data['path']} for Gemini: {e_img}", request_id)
                        prompt_parts_for_api.append(f"[Error processing image: {Path(img_data['path']).name}]")
        # --- Text-Only Model Handling ---
        else:
            _log(f"Calling Gemini API ({model_id}) (text only)", request_id)
            prompt_parts_for_api.append(text_prompt_content)
        
        llm_start_time = time.time()
        response = model.generate_content(prompt_parts_for_api, request_options=request_options)
        llm_end_time = time.time()
        response_data["inference_time_seconds"] = round(llm_end_time - llm_start_time, 3)

        response_data["text_response"] = response.text
        _log(f"Gemini API Call Successful (took {response_data['inference_time_seconds']:.3f}s)", request_id)

    except Exception as e:
        # --- MODIFIED: More specific error message ---
        response_data["text_response"] = f"An error occurred with Gemini API: {e}\n\nNo 'MODIFIED_XML_FILE:' blocks found in LLM response."
        _log(f"Error in call_gemini_api: {e}", request_id)

    return response_data

def plan_xml_edits_with_router(
    user_prompt: str,
    ppt_json_data: dict,
    all_xml_file_paths: list,
    request_id: Optional[str] = None,
    model_id: str = "gpt-5.1-2025-11-13",
    api_keys=None # Added for compatibility with existing calls
) -> dict:
    """
    Calls a lightweight model (OpenAI or Gemini) to produce a structured plan for XML editing.
    Output schema mirrors the previous Gemini planner.
    """
    use_openai = _is_openai_model(model_id)
    
    # Dynamic Planning LLM Selection
    if use_openai:
        planner_model_id = "gpt-5-nano-2025-08-07"
    else:
        planner_model_id = "gemini-2.5-flash"
        
    _log(f"Planning XML edits with {'OpenAI' if use_openai else 'Gemini'} router ({planner_model_id})...", request_id)

    resolved_openai_key = None
    resolved_gemini_key = None
    if api_keys:
        resolved_openai_key = api_keys.get("openai") or api_keys.get("openai_api_key")
        resolved_gemini_key = api_keys.get("gemini") or api_keys.get("gemini_api_key")
    keys = load_api_keys()
    resolved_openai_key = resolved_openai_key or keys.get("openai") or keys.get("openai_api_key")
    resolved_gemini_key = resolved_gemini_key or keys.get("gemini") or keys.get("gemini_api_key")

    if use_openai and not resolved_openai_key:
        return {"error": "OPENAI_API_KEY missing (set in UI or credentials.env)."}
    if not use_openai and not resolved_gemini_key:
        return {"error": "GEMINI_API_KEY missing (set in UI or credentials.env)."}

    files_manifest = {
        "slides": sorted([Path(p).as_posix() for p in all_xml_file_paths if "ppt/slides/slide" in Path(p).as_posix()]),
        "layouts": sorted([Path(p).as_posix() for p in all_xml_file_paths if "ppt/slideLayouts/" in Path(p).as_posix()]),
        "masters": sorted([Path(p).as_posix() for p in all_xml_file_paths if "ppt/slideMasters/" in Path(p).as_posix()]),
        "theme": sorted([Path(p).as_posix() for p in all_xml_file_paths if "ppt/theme/" in Path(p).as_posix()]),
        "other": sorted([Path(p).as_posix() for p in all_xml_file_paths if "ppt/" in Path(p).as_posix() and p.endswith(".xml")])
    }

    system_prompt = (
        "You are an expert PowerPoint XML planner. Given a user instruction, a JSON summary of the presentation, "
        "and a manifest of available XML files, produce a concise JSON planning object indicating which XML files "
        "should be edited and what types of changes are needed. Do NOT output XML."
    )
    prompt = f"""
--- User Instruction ---
{user_prompt}

--- Presentation JSON Summary (truncated ok) ---
```json
{json.dumps(ppt_json_data, indent=2)[:120000]}
```

--- XML Files Manifest ---
```json
{json.dumps(files_manifest, indent=2)}
```

--- Output Requirements ---
Return a single JSON object with:
- targets: array of objects {{file: string, reason: string, operations: string[]}}
- global: array of file paths for global resources (themes/masters/layouts) if relevant
- notes: short rationale
"""

    try:
        if use_openai:
            client = _create_openai_client(resolved_openai_key)
            if client is None:
                return {"error": "Failed to init OpenAI client."}
            response = client.responses.create(
                model=planner_model_id,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt.strip()}]},
                    {"role": "user", "content": [{"type": "input_text", "text": prompt.strip()}]},
                ],
            )
            plan_text = _extract_text_from_openai_response(response).strip()
        else:
            _configure_gemini_client(planner_model_id, resolved_gemini_key)
            model = genai.GenerativeModel(model_name=planner_model_id)
            response = model.generate_content([system_prompt, prompt])
            plan_text = (response.text or "").strip()

        plan = extract_json_from_llm_response(plan_text)
        return plan
    except Exception as e:
        _log(f"Planning Error: {e}", request_id)
        return {"error": str(e)}

# Temporary alias
def plan_xml_edits_with_gemini(*args, **kwargs):
    return plan_xml_edits_with_router(*args, **kwargs)

def get_llm_response(
    user_prompt,
    ppt_json_data,
    xml_file_paths,
    engine_or_model_id="gpt-5.1-2025-11-13",
    image_inputs=None,
    use_pre_analysis=True,
    request_id=None,
    api_key=None,
    edit_history=None,
):
    """
    Orchestrates the two-call process to get the LLM's response.
    1. Uses a heuristic algorithm to identify relevant files.
    2. Calls the user-selected model with only the content of those files.
    This pre-analysis step can be toggled off.
    Returns a dictionary containing the LLM response and the list of files used.
    """
    _log(f"LLM Handler (get_llm_response) Called for: {engine_or_model_id}", request_id)

    relevant_xml_paths = xml_file_paths
    edit_plan = None

    if use_pre_analysis:
        # --- NEW STAGE 1: LLM Planning for XML targets ---
        planner_api_keys = {}
        if api_key:
            if _is_openai_model(engine_or_model_id):
                planner_api_keys["openai"] = api_key
            else:
                planner_api_keys["gemini"] = api_key
        loaded_keys = load_api_keys()
        if "openai" not in planner_api_keys:
            planner_api_keys["openai"] = loaded_keys.get("openai") or loaded_keys.get("openai_api_key")
        if "gemini" not in planner_api_keys:
            planner_api_keys["gemini"] = loaded_keys.get("gemini") or loaded_keys.get("gemini_api_key")

        plan = plan_xml_edits_with_router(
            user_prompt=user_prompt,
            ppt_json_data=ppt_json_data,
            all_xml_file_paths=xml_file_paths,
            request_id=request_id,
            model_id=engine_or_model_id,
            api_keys=planner_api_keys,
        )
        if plan and not plan.get("error"):
            edit_plan = plan
            candidate_files = set()
            for t in plan.get("targets", []) or []:
                f = t.get("file")
                if isinstance(f, str):
                    candidate_files.add(f)
            for g in plan.get("global", []) or []:
                if isinstance(g, str):
                    candidate_files.add(g)
            # Filter to only existing paths from the provided list
            provided = {Path(p).as_posix() for p in xml_file_paths}
            relevant_xml_paths = [p for p in candidate_files if p in provided]
            if relevant_xml_paths:
                _log(f"Planning selected {len(relevant_xml_paths)} XML files.", request_id)
            else:
                _log("Planning returned no matching files; falling back to heuristic.", request_id)
        else:
            _log("Planning failed; falling back to heuristic pre-analysis.", request_id)

        if not relevant_xml_paths:
            # Legacy heuristic fallback
            relevant_xml_paths = get_relevant_xml_files_heuristic(
                user_prompt,
                ppt_json_data,
                xml_file_paths,
            )
            if not relevant_xml_paths:
                return {
                    "text_response": "No changes needed (as determined by pre-analysis).",
                    "model_used": "preanalysis",
                    "inference_time_seconds": 0,
                    "relevant_files": []
                }
            if len(relevant_xml_paths) == len(xml_file_paths):
                _log("First-pass check returned all files (or failed); proceeding with full context.", request_id)
            else:
                _log(f"Proceeding with {len(relevant_xml_paths)} files identified by heuristic.", request_id)
    else:
        _log("Skipping pre-analysis step as requested.", request_id)

    # --- STAGE 2: CALL MAIN LLM WITH REFINED FILE LIST ---
    llm_response_data = {}
    if "gemini" in engine_or_model_id.lower() or "google" in engine_or_model_id.lower():
        llm_response_data = call_gemini_api(
            user_prompt,
            ppt_json_data,
            relevant_xml_paths,
            engine_or_model_id,
            image_inputs,
            request_id=request_id,
            api_key=api_key,
            edit_history=edit_history,
            edit_plan=edit_plan,
        )
    elif any(s in engine_or_model_id.lower() for s in ["gpt", "openai", "o3", "o1", "o4", "gpt-5"]):
        llm_response_data = call_openai_api(
            user_prompt,
            ppt_json_data,
            relevant_xml_paths,
            engine_or_model_id,
            image_inputs,
            request_id=request_id,
            api_key=api_key,
            edit_history=edit_history,
            edit_plan=edit_plan,
        )
    else:
        llm_response_data = {"text_response": f"Error: Unknown model provider for '{engine_or_model_id}'. Please use 'gemini' or 'gpt' in the model name.", "model_used": "N/A", "inference_time_seconds": None}

    llm_response_data["relevant_files"] = relevant_xml_paths
    if edit_plan:
        llm_response_data["planning_plan"] = edit_plan
        llm_response_data["planning_model"] = "gpt-5.1-2025-11-13"
    return llm_response_data

def parse_llm_response_for_xml_changes(llm_text_response):
    # Import from utils to avoid duplication, or keep here if it's considered a handler logic
    from llm.utils import parse_llm_response_for_xml_changes as _parse
    return _parse(llm_text_response)

def call_llm_router(
    user_prompt: str,
    ppt_json_data: dict,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
    preferred_model_id: Optional[str] = None,
) -> str:
    """
    Uses a lightweight model (OpenAI or Gemini) to decide which editing strategy to use.
    """
    use_openai = _is_openai_model(preferred_model_id) if preferred_model_id else True
    
    # Dynamic Router Model Selection
    if use_openai:
        router_model_id = "gpt-5-nano-2025-08-07"
    else:
        router_model_id = "gemini-2.5-flash"
    
    _log(f"Calling LLM Router to decide editing strategy via {router_model_id} (User preferred: {preferred_model_id})...", request_id)

    system_prompt = """
You are a decision-making engine. Your task is to choose the best strategy for editing a PowerPoint presentation based on the user's request.
You have two choices:
1.  `XML_EDIT`: Best for complex, single-slide edits like creating SmartArt, charts, or intricate formatting changes that require direct XML manipulation.
2.  `PYTHON_PPTX_EDIT`: Best for simple, repetitive, multi-slide tasks like text replacement, translation, or applying a consistent style change across the entire deck.

Analyze the user's prompt and the presentation structure.
- If the user asks to "translate the whole deck", "translate all slides", or "rewrite all text", YOU MUST CHOOSE `PYTHON_PPTX_EDIT`.
- If the user asks for a specific visual design change on one slide, choose `XML_EDIT`.

Respond with ONLY the string `XML_EDIT` or `PYTHON_PPTX_EDIT`. Do not provide any explanation.
"""

    prompt = f"""
--- User Prompt ---
{user_prompt}

--- Presentation Summary (high-level) ---
{json.dumps({"slide_count": len(ppt_json_data.get("slides", [])), "slide_titles": [s.get("title", "Untitled") for s in ppt_json_data.get("slides", [])]}, indent=2)}

--- Your Decision ---
"""

    if use_openai:
        router_api_key = api_key
        if not router_api_key:
            keys = load_api_keys()
            router_api_key = keys.get("openai") or keys.get("openai_api_key")

        if not router_api_key:
            _log("Router Error: OpenAI API key not found. Defaulting to XML_EDIT.", request_id)
            return "XML_EDIT"

        client = _create_openai_client(router_api_key)
        if client is None:
            _log("Router Error: Failed to initialize OpenAI client. Defaulting to XML_EDIT.", request_id)
            return "XML_EDIT"

        try:
            response = client.responses.create(
                model=router_model_id,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt.strip()}]},
                    {"role": "user", "content": [{"type": "input_text", "text": prompt.strip()}]},
                ],
            )
            decision = _extract_text_from_openai_response(response).strip().upper()
        except Exception as e:
            _log(f"Router Error: An exception occurred - {e}. Defaulting to XML_EDIT.", request_id)
            return "XML_EDIT"
    else:
        keys = load_api_keys()
        gemini_key = api_key or keys.get("gemini") or keys.get("gemini_api_key")
        if not gemini_key:
            _log("Router Error: Gemini API key not found. Defaulting to XML_EDIT.", request_id)
            return "XML_EDIT"
        try:
            _configure_gemini_client(router_model_id, gemini_key)
            model = genai.GenerativeModel(model_name=router_model_id)
            resp = model.generate_content([system_prompt, prompt])
            decision = (resp.text or "").strip().upper()
        except Exception as e:
            _log(f"Router Error (Gemini): {e}. Defaulting to XML_EDIT.", request_id)
            return "XML_EDIT"

    if "PYTHON_PPTX_EDIT" in decision:
        _log("LLM Router decision: PYTHON_PPTX_EDIT", request_id)
        return "PYTHON_PPTX_EDIT"
    if "XML_EDIT" in decision:
        _log("LLM Router decision: XML_EDIT", request_id)
        return "XML_EDIT"
    _log(f"Router Warning: Unexpected response '{decision}'. Defaulting to XML_EDIT.", request_id)
    return "XML_EDIT"

def generate_content_for_python_pptx(
    user_prompt: str,
    ppt_json_data: dict,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
    model_id: str = "gpt-5.1-2025-11-13"
) -> dict:
    """
    Generates a structured JSON object of content needed for a python-pptx script.
    """
    _log("Calling LLM to generate content for python-pptx script...", request_id)
    effective_model_id = model_id or "gpt-5.1-2025-11-13"
    use_openai = _is_openai_model(effective_model_id)

    def _call_gemini_content():
        gemini_model_id = effective_model_id if not use_openai else "gemini-3-pro-preview"
        gemini_key = api_key
        if not gemini_key:
            keys = load_api_keys()
            gemini_key = keys.get("gemini") or keys.get("gemini_api_key")

        if not gemini_key:
            return {"error": "API key not found."}

        _configure_gemini_client(gemini_model_id, gemini_key)
        base_generation_config = {"response_mime_type": "application/json"}
        generation_config = _build_gemini_generation_config(gemini_model_id, base_generation_config)
        model_kwargs = {"model_name": gemini_model_id}
        if generation_config:
            model_kwargs["generation_config"] = generation_config
        model = genai.GenerativeModel(**model_kwargs)

        response = model.generate_content([system_prompt, prompt])
        response_text = response.text.strip()
        return extract_json_from_llm_response(response_text)

    system_prompt = """
You are a content generation specialist. Your task is to analyze a user's request to edit a PowerPoint presentation and extract or generate ONLY the data and content needed to perform the edit.

**CRITICAL RULE: You MUST NOT generate any code (Python, XML, etc.). Your ONLY output must be a single, valid JSON object.**

The JSON object should contain the necessary information for a separate coding step. For example:
- For a translation request, you will provide a mapping of original text to translated text.
- For a data update request, you will provide the new data points.
- For a summarization request, you will provide the summarized text for each slide.

Analyze the user's prompt and the provided JSON summary of the presentation, and generate the content required.
"""

    prompt = f"""
--- User Prompt ---
{user_prompt}

--- Full Presentation JSON Summary ---
{json.dumps(ppt_json_data, indent=2)}

--- Required Content (JSON Output Only) ---
"""

    try:
        if use_openai:
            openai_key = api_key
            if not openai_key:
                keys = load_api_keys()
                openai_key = keys.get("openai") or keys.get("openai_api_key")
            if not openai_key:
                return {"error": f"OpenAI API key not provided (set in UI or {CREDENTIALS_FILE})."}

            client = _create_openai_client(openai_key)
            if client is None:
                return {"error": "Failed to initialize OpenAI client."}
            try:
                response = client.responses.create(
                    model=effective_model_id,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": system_prompt.strip()}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt.strip()}]},
                    ],
                )
                response_text = _extract_text_from_openai_response(response).strip()
                return extract_json_from_llm_response(response_text)
            except Exception as oe:
                if _is_insufficient_quota_error(oe):
                    _log("OpenAI quota exceeded for python-pptx content generation; falling back to Gemini.", request_id)
                    return _call_gemini_content()
                raise

        return _call_gemini_content()

    except Exception as e:
        _log(f"Content Generation Error: {e}", request_id)
        return {"error": f"Failed to generate content: {str(e)}"}

def generate_python_pptx_code(
    user_prompt: str,
    ppt_json_data: dict,
    generated_content: dict,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
    model_id: str = "gpt-5.1-2025-11-13"
) -> str:
    """
    Generates a python-pptx script to modify a presentation.
    """
    _log("Calling LLM to generate python-pptx code...", request_id)
    effective_model_id = model_id or "gpt-5.1-2025-11-13"
    use_openai = _is_openai_model(effective_model_id)

    def _call_gemini_code():
        gemini_model_id = effective_model_id if not use_openai else "gemini-3-pro-preview"
        gemini_key = api_key
        if not gemini_key:
            keys = load_api_keys()
            gemini_key = keys.get("gemini") or keys.get("gemini_api_key")

        if not gemini_key:
            return "print('Error: API key not found.')"

        _configure_gemini_client(gemini_model_id, gemini_key)
        generation_config = _build_gemini_generation_config(gemini_model_id)
        model_kwargs = {"model_name": gemini_model_id}
        if generation_config:
            model_kwargs["generation_config"] = generation_config
        model = genai.GenerativeModel(**model_kwargs)

        response = model.generate_content([system_prompt, prompt])
        return response.text.strip()

    system_prompt = """
You are an expert Python programmer specializing in the `python-pptx` library. Your task is to write a complete, executable Python script that will modify a PowerPoint presentation.

**CRITICAL RULES:**
1.  **DO NOT** write anything other than the Python code. No explanations, no comments before or after the code block.
2.  The script MUST be self-contained and import all necessary libraries (`sys`, `json`, `pptx`).
3.  The script will be executed from the command line with two arguments: the path to the `.pptx` file and the path to a JSON file containing the content.
4.  You MUST include the boilerplate `if __name__ == "__main__":` to parse these arguments.
5.  The core logic should be in a function called `apply_edits(pptx_path, content_path)`.
6.  The `content` loaded from the JSON file will be the data you need to apply the edits. Use it as the source of truth for the changes.
7.  After modifying the presentation object, you MUST save it back to the **original `pptx_path`**.

Below is the context you need to write the script.
"""

    prompt = f"""
--- User's Original Prompt ---
{user_prompt}

--- Presentation Structure (for context) ---
{json.dumps(ppt_json_data, indent=2)}

--- Pre-Generated Content (to be used by your script) ---
{json.dumps(generated_content, indent=2)}

--- Your Python Script (Code Only) ---
"""

    try:
        if use_openai:
            openai_key = api_key
            if not openai_key:
                keys = load_api_keys()
                openai_key = keys.get("openai") or keys.get("openai_api_key")
            if not openai_key:
                return "print('Error: OpenAI API key not provided (set in UI or credentials.env).')"

            client = _create_openai_client(openai_key)
            if client is None:
                return "print('Error: Failed to initialize OpenAI client.')"
            try:
                response = client.responses.create(
                    model=effective_model_id,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": system_prompt.strip()}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt.strip()}]},
                    ],
                )
                code = _extract_text_from_openai_response(response).strip()
            except Exception as oe:
                if _is_insufficient_quota_error(oe):
                    _log("OpenAI quota exceeded for python-pptx code generation; falling back to Gemini.", request_id)
                    code = _call_gemini_code()
                else:
                    raise
        else:
            code = _call_gemini_code()

        if code.startswith("```python"):
            code = code[9:]
        if code.endswith("```"):
            code = code[:-3]

        _log("Successfully generated python-pptx code.", request_id)
        return code.strip()

    except Exception as e:
        _log(f"Code Generation Error: {e}", request_id)
        return f"print('Error during code generation: {str(e)}')"

def generate_transformation_instructions(
    judge_model: str,
    original_ppt_json: dict,
    ground_truth_ppt_json: dict,
    original_slide_images_b64: Optional[List[str]] = None,
    ground_truth_slide_images_b64: Optional[List[str]] = None,
    request_id: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
) -> dict:
    """
    Generate detailed instructions via two separate LLM calls.
    """
    if not api_key:
        keys = load_api_keys()
        api_key = keys.get("gemini") or keys.get("gemini_api_key")
    if not api_key:
        return {"error": f"API key not found in {CREDENTIALS_FILE}"}

    try:
        _configure_gemini_client(judge_model, api_key)

        def _truncate_json_for_prompt(d):
            try:
                s = json.dumps(d, indent=2)
                return s if len(s) <= 150000 else s[:75000] + "\n...TRUNCATED...\n" + s[-75000:]
            except Exception:
                return str(d)[:150000]

        # --- Call 1: JSON-only overview generation ---
        overview_base_config = {
            "response_mime_type": "application/json",
            "temperature": temperature,
            "top_p": 0.9,
            "top_k": 1,
        }
        overview_generation_config = _build_gemini_generation_config(judge_model, overview_base_config)
        overview_kwargs = {
            "model_name": judge_model,
            "safety_settings": {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        }
        if overview_generation_config:
            overview_kwargs["generation_config"] = overview_generation_config
        json_model = genai.GenerativeModel(**overview_kwargs)

        system_prompt_json = (
            "You are an expert technical writer for presentation editing workflows. "
            "Given ONLY the Original deck and the Ground Truth deck, produce actionable, specific instructions to convert "
            "Original into Ground Truth. Focus on content and structure inferred from JSON; do not reference any predictions."
        )

        json_payload = f"""
--- ORIGINAL (JSON summary) ---
```json
{_truncate_json_for_prompt(original_ppt_json)}
```

--- GROUND TRUTH (JSON summary) ---
```json
{_truncate_json_for_prompt(ground_truth_ppt_json)}
```
"""

        overview_spec = (
            "Return a single JSON object with keys: overview_instructions (multi-sentence, stepwise where helpful), "
            "and notes (optional)."
        )

        overview_resp = json_model.generate_content([system_prompt_json, json_payload, overview_spec])
        overview_text = (overview_resp.text or "").strip()
        try:
            overview_obj = extract_json_from_llm_response(overview_text)
        except Exception:
            overview_obj = {"overview_instructions": overview_text}

        overview_instructions = str(overview_obj.get("overview_instructions", "")).strip()
        notes_text = str(overview_obj.get("notes", "")).strip()

        # --- Call 2: Images-only visual instructions (batch by 5 slide pairs) ---
        def _norm_b64(s):
            try:
                return s.split(",")[-1]
            except Exception:
                return s

        ori_imgs = [i for i in (original_slide_images_b64 or []) if i]
        gt_imgs = [i for i in (ground_truth_slide_images_b64 or []) if i]
        max_pairs = min(len(ori_imgs), len(gt_imgs))

        visual_base_config = {
            "response_mime_type": "application/json",
            "temperature": temperature,
            "top_p": 0.9,
            "top_k": 1,
        }
        visual_generation_config = _build_gemini_generation_config(
            judge_model,
            visual_base_config,
            use_high_res_media=True,
        )
        visual_kwargs = {
            "model_name": judge_model,
            "safety_settings": {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        }
        if visual_generation_config:
            visual_kwargs["generation_config"] = visual_generation_config
        visual_model = genai.GenerativeModel(**visual_kwargs)

        system_prompt_visual = (
            "You are a meticulous presentation visual editor. Using ONLY images of the Original (before) and Ground Truth (after) slides, "
            "write imperative, detailed instructions describing the visual/layout/style changes required to transform the Original into the Ground Truth."
        )

        visual_spec = (
            "Return JSON with a single key visual_instructions (multi-sentence, concrete, slide-number aware when identifiable)."
        )

        batch_size = 5
        visual_chunks = []
        if max_pairs > 0:
            indices = list(range(max_pairs))
            batches = [indices[i:i+batch_size] for i in range(0, len(indices), batch_size)]
            # If last batch is very small (<3) and there is a previous batch, merge it
            if len(batches) >= 2 and len(batches[-1]) < 3:
                batches[-2].extend(batches[-1])
                batches = batches[:-1]

            for batch in batches:
                parts = [system_prompt_visual]
                parts.append(f"Batch slide indices (1-based): {', '.join(str(i+1) for i in batch)}")
                parts.append("ORIGINAL_SLIDES")
                for i in batch:
                    b64 = _norm_b64(ori_imgs[i])
                    parts.append(f"ORIGINAL_SLIDE_{i+1}")
                    parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})
                parts.append("GROUND_TRUTH_SLIDES")
                for i in batch:
                    b64 = _norm_b64(gt_imgs[i])
                    parts.append(f"GROUND_TRUTH_SLIDE_{i+1}")
                    parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})
                parts.append(visual_spec)

                vresp = visual_model.generate_content(parts)
                vtext = (vresp.text or "").strip()
                try:
                    vobj = extract_json_from_llm_response(vtext)
                    vchunk = str(vobj.get("visual_instructions", "")).strip()
                except Exception:
                    vchunk = vtext
                if vchunk:
                    visual_chunks.append(vchunk)

        visual_instructions = "\n".join(visual_chunks).strip()

        return {
            "overview_instructions": overview_instructions,
            "visual_instructions": visual_instructions,
            "notes": notes_text,
        }
    except Exception as e:
        return {"error": f"Instruction generation error: {e}"}
