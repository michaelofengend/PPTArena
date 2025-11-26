import json
import os
import re
import csv
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import openai
from typing import Optional

CREDENTIALS_FILE = "credentials.env"
API_KEYS = {}

def _log(message, request_id=None):
    """Helper function for logging with an optional request ID."""
    if request_id:
        print(f"[{request_id}] {message}")
    else:
        print(message)
    # Mirror messages to progress stream if available
    try:
        from ..progress import append as _progress_append
        if request_id:
            _progress_append(request_id, str(message))
    except Exception:
        pass

def _log_token_info(model, prompt, token_count, log_type="gemini", request_id=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_str = f"Model: {model} | {log_type} prompt tokens: {token_count} | Prompt chars: {len(prompt)}"
    _log(log_str, request_id)
    # Append to CSV (add columns if not present)
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "processing_log.csv")
    colname = f"{log_type}_tokens"
    required_cols = ["Timestamp", "LLMEngineUsed", colname, "PromptChars"]
    # Read all rows and header
    rows = []
    try:
        with open(log_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames if reader.fieldnames else []
            for row in reader:
                rows.append(row)
    except Exception:
        header = []
    # Ensure required columns are present
    if header is None:
        header = []
    for col in required_cols:
        if col not in header:
            header.append(col)
    # Prepare new row
    row = {h: '' for h in header}
    row['Timestamp'] = timestamp
    row['LLMEngineUsed'] = model
    row[colname] = token_count
    row['PromptChars'] = len(prompt)
    rows.append(row)
    # Write all rows back with updated header
    try:
        with open(log_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
    except Exception as e:
        print(f"[TokenLog] Error writing to CSV: {e}")

def load_api_keys():
    """Loads API keys from credentials.env"""
    global API_KEYS
    if not API_KEYS: # Load only once
        API_KEYS = {} # Initialize as dict
        try:
            if os.path.exists(CREDENTIALS_FILE):
                with open(CREDENTIALS_FILE, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            if key.strip().upper() == "OPENAI_API_KEY":
                                API_KEYS["openai"] = value.strip()
                            elif key.strip().upper() == "OPENAI_ORG_ID":
                                API_KEYS["openai_org_id"] = value.strip()
                            elif key.strip().upper() == "GEMINI_API_KEY":
                                API_KEYS["gemini"] = value.strip()
            else:
                _log(f"Warning: {CREDENTIALS_FILE} not found. API calls will likely fail.")
        except Exception as e:
            _log(f"Error loading {CREDENTIALS_FILE}: {e}")
            API_KEYS = {} 
    return API_KEYS

def _create_openai_client(api_key: Optional[str] = None):
    keys = load_api_keys()
    resolved_key = api_key or keys.get("openai")
    if not resolved_key:
        return None
    org_id = keys.get("openai_org_id")
    try:
        if org_id:
            return openai.OpenAI(api_key=resolved_key, organization=org_id)
        return openai.OpenAI(api_key=resolved_key)
    except Exception as e:
        _log(f"Error creating OpenAI client: {e}", None)
        return None

def _is_openai_model(model_id: Optional[str]) -> bool:
    if not model_id:
        return False
    lowered = model_id.lower()
    return any(token in lowered for token in ["gpt", "openai", "o1", "o3", "o4", "gpt-5"])

def _extract_text_from_openai_response(resp) -> str:
    text_out = getattr(resp, "output_text", None)
    if text_out:
        return text_out
    try:
        parts = []
        for out in getattr(resp, "output", []) or []:
            for c in getattr(out, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    parts.append(str(t))
        if parts:
            return "\n".join(parts)
    except Exception:
        pass
    try:
        if hasattr(resp, "to_dict"):
            return json.dumps(resp.to_dict())
    except Exception:
        pass
    return str(resp)

def _is_insufficient_quota_error(err: Exception) -> bool:
    if err is None:
        return False
    code_attr = getattr(err, "code", None)
    if isinstance(code_attr, str) and code_attr.lower() == "insufficient_quota":
        return True
    status_code = getattr(err, "status_code", None)
    if status_code == 429:
        return True
    try:
        err_str = str(err).lower()
    except Exception:
        err_str = ""
    if "insufficient_quota" in err_str:
        return True
    if "exceeded your current quota" in err_str:
        return True
    return False

def extract_json_from_llm_response(response_text: str) -> dict:
    """
    Robustly extracts JSON from LLM responses that may contain extra text,
    markdown formatting, or multiple JSON objects.
    """
    if not response_text or not response_text.strip():
        raise ValueError("Empty response text")
    
    try:
        # First, try to parse the response directly as JSON
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        pass
    
    # Try to extract from markdown code blocks
    json_patterns = [
        r'```json\s*\n(.*?)\n\s*```',  # Standard json code block
        r'```\s*\n(.*?)\n\s*```',      # Generic code block
        r'`(.*?)`',                    # Inline code
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, response_text, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
    
    # Try to find JSON objects by searching for balanced braces
    text = response_text.strip()
    
    # Find the first opening brace
    start_idx = text.find('{')
    if start_idx == -1:
        raise ValueError("No JSON object found in response")
    
    # Find the matching closing brace by counting nested braces
    brace_count = 0
    end_idx = start_idx
    
    for i in range(start_idx, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                end_idx = i
                break
    
    if brace_count != 0:
        raise ValueError("Unbalanced braces in JSON response")
    
    # Extract and parse the JSON
    json_str = text[start_idx:end_idx + 1]
    return json.loads(json_str)

def _configure_gemini_client(model_id: str, api_key: str) -> None:
    """
    Configure the Gemini client, ensuring Gemini 3 models hit the v1alpha API.
    """
    # Some google-generativeai versions choke on http_options; use a minimal, compatible configure.
    genai.configure(api_key=api_key)


def _build_gemini_generation_config(
    model_id: str,
    base_config: Optional[dict] = None,
    use_high_res_media: bool = False,
) -> Optional[dict]:
    """
    Merge common Gemini configuration defaults.
    Gemini 3 Pro requires explicit thinking level (high) and temperature 1.0.
    """
    config: dict = dict(base_config or {})
    if "gemini-3" in model_id:
        config.setdefault("temperature", 1.0)
        # Some SDK versions reject extra fields; avoid unsupported keys like thinking_level/media_resolution.
    return config or None

def parse_llm_response_for_xml_changes(llm_text_response):
    """
    Extract modified XML blocks from LLM output in multiple tolerant formats:
    1) MODIFIED_XML_FILE: <path>```xml ... ```
    2) MODIFIED_XML_FILE: <path>``` ... ``` (no language tag)
    3) MODIFIED_XML_FILE: <path> followed by raw XML until the next tag or end
    4) JSON-shaped output containing { "modified_files": { path: xml, ... } }
    """
    modified_files = {}
    text = llm_text_response or ""

    # First try JSON
    try:
        parsed = extract_json_from_llm_response(text)
        if isinstance(parsed, dict):
            mf = parsed.get("modified_files") or parsed.get("files") or parsed.get("xml_files")
            if isinstance(mf, dict):
                # Accept only .xml keys
                for k, v in mf.items():
                    if isinstance(k, str) and k.endswith('.xml') and isinstance(v, str) and v.strip():
                        modified_files[k] = v.strip()
                if modified_files:
                    return modified_files
    except Exception:
        pass

    # Regex approach with multiple patterns
    fence_patterns = [
        re.compile(r"MODIFIED_XML_FILE:\s*(?P<filename>[a-zA-Z0-9./\-_]+?\.xml)\s*```xml\n(?P<xml_content>.+?)\n```", re.DOTALL),
        re.compile(r"MODIFIED_XML_FILE:\s*(?P<filename>[a-zA-Z0-9./\-_]+?\.xml)\s*```\n(?P<xml_content>.+?)\n```", re.DOTALL),
    ]
    for pat in fence_patterns:
        for m in pat.finditer(text):
            filename = m.group('filename').strip()
            xml_content = m.group('xml_content').strip()
            modified_files[filename] = xml_content
    if modified_files:
        return modified_files

    # Fallback: scan segments between tags
    tag_pat = re.compile(r"MODIFIED_XML_FILE:\s*([a-zA-Z0-9./\-_]+?\.xml)")
    starts = list(tag_pat.finditer(text))
    for i, m in enumerate(starts):
        filename = m.group(1).strip()
        seg_start = m.end()
        seg_end = starts[i + 1].start() if (i + 1) < len(starts) else len(text)
        segment = text[seg_start:seg_end].strip()
        if not segment:
            continue
        # If contains a code fence, grab inside; else take segment as XML
        cf = re.search(r"```(?:xml)?\n([\s\S]+?)\n```", segment)
        if cf:
            content = cf.group(1).strip()
        else:
            content = segment.strip()
        # Basic sanity: should look like XML
        if '<' in content and '>' in content:
            modified_files[filename] = content

    return modified_files
