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
    _log_token_info
)
from llm.prompt import (
    build_xml_editing_prompt,
    build_planning_prompt,
    build_judge_prompt
)
from ppt import (
    pptx_to_json,
    diff_pptx_json,
    format_diff_for_judge,
    export_slides_to_images,
    convert_pptx_to_base64_images
)

# --- Main LLM Interaction Functions ---

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
    """
    Handles calls to the OpenAI API (Chat Completions).
    Supports text-only and multimodal (image) inputs.
    """
    client = _create_openai_client(api_key)
    if not client:
        _log("Error: OpenAI client could not be created.", request_id)
        return None

    # Construct the system and user messages
    messages = build_xml_editing_prompt(
        user_prompt,
        ppt_json_data,
        xml_file_paths,
        image_inputs,
        edit_history,
        edit_plan
    )

    try:
        start_time = time.time()
        
        # Check for O1/O3 models which don't support system messages or temperature
        is_reasoning_model = any(x in model_id for x in ["o1", "o3"])
        
        if is_reasoning_model:
            # For reasoning models, combine system prompt into user message
            # and remove system message from list
            system_content = next((m["content"] for m in messages if m["role"] == "system"), "")
            user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
            
            # If user content is a list (multimodal), prepend system text to the first text part
            if isinstance(user_content, list):
                if user_content and user_content[0]["type"] == "text":
                    user_content[0]["text"] = f"Instructions:\n{system_content}\n\nUser Request:\n{user_content[0]['text']}"
                else:
                    user_content.insert(0, {"type": "text", "text": f"Instructions:\n{system_content}"})
            else:
                user_content = f"Instructions:\n{system_content}\n\nUser Request:\n{user_content}"
            
            # Reconstruct messages list with only user message
            messages = [{"role": "user", "content": user_content}]
            
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                # reasoning_effort="high" # Optional, if supported
            )
        elif "gpt-5" in model_id.lower():
             # Hypothetical GPT-5 handling (same as standard for now)
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.0,
                max_tokens=16384, 
            )
        else:
            # Standard GPT-4o / GPT-4 Turbo handling
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.0,
                max_tokens=4096,
            )

        duration = time.time() - start_time
        
        # Extract response text
        response_text = response.choices[0].message.content
        
        # Log usage
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        _log_token_info(model_id, input_tokens, output_tokens, duration, request_id)

        return {
            "response_text": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration": duration
        }

    except Exception as e:
        _log(f"Error calling OpenAI API: {e}", request_id)
        return None


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
    """
    Handles calls to the Google Gemini API.
    """
    if not _configure_gemini_client(api_key):
        _log("Error: Gemini client could not be configured.", request_id)
        return None

    # Construct the prompt parts
    # Note: build_xml_editing_prompt returns OpenAI-style messages.
    # We need to adapt this for Gemini or use a Gemini-specific builder.
    # For simplicity, we'll reuse the logic but extract the content.
    
    messages = build_xml_editing_prompt(
        user_prompt,
        ppt_json_data,
        xml_file_paths,
        image_inputs,
        edit_history,
        edit_plan
    )
    
    system_instruction = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_message_content = next((m["content"] for m in messages if m["role"] == "user"), "")

    model = genai.GenerativeModel(
        model_name=model_id,
        system_instruction=system_instruction,
        generation_config=_build_gemini_generation_config(model_id)
    )

    prompt_parts_for_api = []
    
    # Handle multimodal input
    if isinstance(user_message_content, list):
        for part in user_message_content:
            if part["type"] == "text":
                prompt_parts_for_api.append(part["text"])
            elif part["type"] == "image_url":
                # Extract base64 data
                base64_data = part["image_url"]["url"].split(",")[1]
                image_data = base64.b64decode(base64_data)
                prompt_parts_for_api.append({
                    "mime_type": "image/png",
                    "data": image_data
                })
    else:
        prompt_parts_for_api.append(user_message_content)

    try:
        start_time = time.time()
        
        # Safety settings
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

        response = model.generate_content(
            prompt_parts_for_api,
            safety_settings=safety_settings,
            request_options={"timeout": 600}
        )
        
        duration = time.time() - start_time
        
        response_text = response.text
        
        # Usage metadata (if available)
        input_tokens = response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0
        output_tokens = response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0
        
        _log_token_info(model_id, input_tokens, output_tokens, duration, request_id)

        return {
            "response_text": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration": duration
        }

    except Exception as e:
        _log(f"Error calling Gemini API: {e}", request_id)
        return None


def plan_xml_edits_with_router(
    user_prompt,
    ppt_json_data,
    xml_file_paths,
    api_key=None,
    request_id=None
):
    """
    Uses a fast LLM to plan which XML files need editing.
    """
    _log("Planning XML edits...", request_id)
    
    messages = build_planning_prompt(user_prompt, ppt_json_data, xml_file_paths)
    
    client = _create_openai_client(api_key)
    if not client:
        return None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", # Use a cheaper/faster model for planning
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        
        plan_json = extract_json_from_llm_response(response.choices[0].message.content)
        _log(f"Edit Plan: {json.dumps(plan_json, indent=2)}", request_id)
        return plan_json
        
    except Exception as e:
        _log(f"Error planning edits: {e}", request_id)
        return None


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
    Main entry point for getting LLM response.
    Orchestrates planning (optional) and execution.
    """
    
    edit_plan = None
    if use_pre_analysis:
        edit_plan = plan_xml_edits_with_router(
            user_prompt,
            ppt_json_data,
            xml_file_paths,
            api_key=api_key,
            request_id=request_id
        )

    if _is_openai_model(engine_or_model_id):
        return call_openai_api(
            user_prompt,
            ppt_json_data,
            xml_file_paths,
            model_id=engine_or_model_id,
            image_inputs=image_inputs,
            request_id=request_id,
            api_key=api_key,
            edit_history=edit_history,
            edit_plan=edit_plan
        )
    else:
        return call_gemini_api(
            user_prompt,
            ppt_json_data,
            xml_file_paths,
            model_id=engine_or_model_id,
            image_inputs=image_inputs,
            request_id=request_id,
            api_key=api_key,
            edit_history=edit_history,
            edit_plan=edit_plan
        )


def parse_llm_response_for_xml_changes(llm_response_text):
    """
    Parses the LLM response to extract XML changes.
    Expected format:
    ```xml:path/to/file.xml
    <content>...</content>
    ```
    or JSON format.
    """
    changes = {}
    
    # Try to parse as JSON first (if the model decided to output JSON)
    try:
        json_data = extract_json_from_llm_response(llm_response_text)
        if json_data and isinstance(json_data, dict):
            # Normalize keys if needed, but assume they are paths
            return json_data
    except:
        pass

    # Regex for fenced code blocks with filename
    # Matches ```xml:path/to/file.xml ... ```
    pattern = r"```(?:xml|json)?:?([^\n]+)\n(.*?)```"
    matches = re.finditer(pattern, llm_response_text, re.DOTALL)
    
    for match in matches:
        filename = match.group(1).strip()
        content = match.group(2)
        
        # Clean up filename if it has extra chars
        filename = filename.split(':')[-1].strip()
        
        changes[filename] = content.strip()
        
    return changes


def call_llm_judge(
    ground_truth_pptx,
    prediction_pptx,
    instruction,
    model_id="gpt-4o",
    api_key=None,
    request_id=None
):
    """
    Evaluates the prediction against the ground truth using an LLM judge.
    """
    _log("Starting LLM Judge evaluation...", request_id)
    
    # 1. Convert to JSON for structural comparison
    try:
        gt_json = pptx_to_json(ground_truth_pptx)
        pred_json = pptx_to_json(prediction_pptx)
        
        diff_result = diff_pptx_json(gt_json, pred_json)
        diff_text = format_diff_for_judge(diff_result, instruction)
        
    except Exception as e:
        _log(f"Error during JSON diff: {e}", request_id)
        diff_text = "Error generating structural diff."

    # 2. Convert to images for visual comparison
    try:
        gt_images = convert_pptx_to_base64_images(ground_truth_pptx)
        pred_images = convert_pptx_to_base64_images(prediction_pptx)
    except Exception as e:
        _log(f"Error generating images for judge: {e}", request_id)
        gt_images = []
        pred_images = []

    # 3. Construct Judge Prompt
    messages = build_judge_prompt(
        instruction,
        diff_text,
        gt_images,
        pred_images
    )
    
    # 4. Call LLM
    client = _create_openai_client(api_key)
    if not client:
        return None
        
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.0,
            max_tokens=2000
        )
        
        judge_output = response.choices[0].message.content
        return judge_output
        
    except Exception as e:
        _log(f"Error calling LLM Judge: {e}", request_id)
        return None
