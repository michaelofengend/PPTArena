import json
import re
import os
from pathlib import Path

def _read_xml_file_content(xml_file_path):
    """Reads the content of a single XML file."""
    try:
        with open(xml_file_path, 'r', encoding='utf-8') as f_xml:
            return f_xml.read()
    except Exception as e:
        print(f"Error reading XML file {xml_file_path}: {e}")
        return f"Error reading file: {Path(xml_file_path).name}"

def _construct_llm_input_prompt(
    user_prompt,
    ppt_json_data,
    xml_file_paths,
    image_inputs_present=False,
    num_slides_with_images=0,
    edit_history=None,
    request_id=None,
    edit_plan=None,
):
    """
    Helper function to construct the detailed prompt for the LLM.
    image_inputs_present: Boolean indicating if image data is part of the context for vision models.
    num_slides_with_images: Integer, number of slides for which images are provided.
    """
    json_summary_for_prompt = json.dumps(ppt_json_data, indent=2)
    if len(json_summary_for_prompt) > 150000: 
        json_summary_for_prompt = (
            f"JSON summary is too large to include fully in this section. "
            f"Total slides: {len(ppt_json_data.get('slides', []))}. "
            f"First slide shapes count: {len(ppt_json_data.get('slides', [{}])[0].get('shapes', [])) if ppt_json_data.get('slides') else 'N/A'}."
            f" (Full JSON was prepared but summarized for this prompt view)"
        )
    
    slide_xml_files = sorted(
        [p for p in xml_file_paths if "ppt/slides/slide" in Path(p).as_posix()],
        key=lambda x: int(re.search(r'slide(\d+)\.xml', Path(x).name).group(1)) if re.search(r'slide(\d+)\.xml', Path(x).name) else float('inf')
    )
    other_xml_files = [p for p in xml_file_paths if p not in slide_xml_files]

    per_slide_prompt_parts = ["\n\n--- Per-Slide Information (XML and corresponding Image if provided) ---"]
    
    slide_xml_chars_total = 0
    slides_xml_processed_count = 0

    for slide_xml_path_str in slide_xml_files:
        slide_xml_path_obj = Path(slide_xml_path_str)
        slide_number_match = re.search(r'slide(\d+)\.xml', slide_xml_path_obj.name)
        if not slide_number_match:
            print(f"Warning: Could not determine slide number from filename: {slide_xml_path_obj.name}")
            continue
        
        slide_num_from_filename = int(slide_number_match.group(1))
        slide_xml_content = _read_xml_file_content(slide_xml_path_str)
        
        current_slide_xml_part = f"\n\n--- Slide {slide_num_from_filename} ({slide_xml_path_obj.as_posix()}) ---"
        if image_inputs_present and slide_num_from_filename <= num_slides_with_images:
            current_slide_xml_part += f"\n(An image for Slide {slide_num_from_filename} is provided as part of the multimodal input.)"
        
        if len(slide_xml_content) > 30000:
            slide_xml_display_content = f"{slide_xml_content[:15000]}...\n...{slide_xml_content[-15000:]} (Truncated)"
            slide_xml_chars_total += 30000
        else:
            slide_xml_display_content = slide_xml_content
            slide_xml_chars_total += len(slide_xml_content)

        current_slide_xml_part += f"\nXML Content:\n```xml\n{slide_xml_display_content}\n```"
        per_slide_prompt_parts.append(current_slide_xml_part)
        slides_xml_processed_count += 1
        
        if slide_xml_chars_total > 300000:
            per_slide_prompt_parts.append("\n\n--- Further slide XML content truncated due to overall size limit for slide XMLs. ---")
            break

    aggregated_other_xml_content = "\n\n--- Other Ancillary XML Content (e.g., theme, presentation properties) ---\n"
    total_other_xml_chars = 0
    other_xml_files_processed_count = 0

    for xml_path_str in other_xml_files:
        xml_path_obj = Path(xml_path_str)
        content = _read_xml_file_content(xml_path_str)
        
        if len(content) > 50000 and other_xml_files_processed_count > 3:
             current_other_xml_part = f"\n\n--- XML File: {xml_path_obj.as_posix()} (Content truncated due to length) ---\n{content[:1000]}...\n--- End ---\n"
             total_other_xml_chars += 1000 
        else:
            current_other_xml_part = f"\n\n--- XML File: {xml_path_obj.as_posix()} ---\n{content}\n--- End ---\n"
            total_other_xml_chars += len(content)
        
        aggregated_other_xml_content += current_other_xml_part
        other_xml_files_processed_count +=1
        
        if total_other_xml_chars > 200000:
            aggregated_other_xml_content += "\n\n--- Further ancillary XML content truncated due to overall size limit for other XMLs. ---\n"
            break

    # --- MODIFIED: Restructured the prompt for better LLM adherence ---

    # Part 1: Persona and context setting
    prompt_context_parts = [
        "You are an expert AI assistant that modifies PowerPoint presentations by editing their underlying XML structure. You may also receive images of each slide to provide visual context.",
        "You will now be provided with the complete context for a presentation, which includes:",
        "1. A user's natural language modification request.",
        "2. A JSON summary of the presentation's content.",
        "3. The raw XML content for each slide and other presentation components (like themes, layouts, etc.)."
    ]
    if image_inputs_present:
        prompt_context_parts.append("4. An image of each slide, which will be provided as multimodal input for visual context.")

    # Part 2: The actual data payload
    prompt_data_parts = [
        "\n\n--- PRESENTATION CONTEXT & DATA ---",
        f"\nUser's Request:\n{user_prompt}",
        f"\n\nJSON Summary:\n{json_summary_for_prompt}",
        "".join(per_slide_prompt_parts),
        aggregated_other_xml_content
    ]

    # Optional: Include an explicit edit plan from a planning step to guide XML generation
    if edit_plan:
        try:
            plan_json = json.dumps(edit_plan, indent=2)
        except Exception:
            plan_json = str(edit_plan)
        prompt_data_parts.insert(1, f"\n\n--- EDIT PLAN (from planning step) ---\n```json\n{plan_json}\n```\n")

    if edit_history:
        history_lines = ["\n\n--- Previous Edits ---"]
        for idx, h in enumerate(edit_history, 1):
            history_lines.append(f"Edit {idx} Prompt: {h.get('prompt', '')}")
            history_lines.append(f"Edit {idx} Response: {h.get('response', '')}")
        prompt_data_parts.insert(1, "\n".join(history_lines))

    # Part 3: The final, critical instruction set
    prompt_instruction_parts = [
        "\n\n--- TASK & OUTPUT FORMAT ---",
        "\nBased on all the provided context (the user's request, JSON, and all XML files), your task is to identify which XML file(s) must be changed to fulfill the request and generate the complete, new content for each of those files.",
        "\n**CRITICAL: YOUR RESPONSE MUST FOLLOW THESE RULES EXACTLY:**",
        "- If you determine that one or more XML files need to be modified, you MUST format your response by providing each modified file's content within a specific block.",
        "- For EACH modified file, you MUST start the block with the tag `MODIFIED_XML_FILE: [original_filename_e.g.,_ppt/slides/slide1.xml]` followed by the code block.",
        "- Example of the required format for ONE modified file:",
        "MODIFIED_XML_FILE: ppt/slides/slide1.xml",
        "```xml",
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>",
        "<p:sld ...>",
        "  ",
        "</p:sld>",
        "```",
        "- The XML you provide MUST be complete and well-formed for that specific file.",
        "- Use the exact internal file path (e.g., `ppt/slides/slide1.xml`, `ppt/theme/theme1.xml`) as seen in the context above.",
        "- **DO NOT** include any extra conversation, commentary, or explanations outside of the `MODIFIED_XML_FILE:` blocks. If no changes are needed, simply respond with 'No changes needed.'."
    ]
    prompt_instruction_parts.extend([
        "- Preserve the existing XML structure unless a change is explicitly required. Keep all mandatory nodes such as `<p:nvSpPr>`, `<p:cNvPr>`, `<a:nvPr>`, and closing tags in their original order.",
        "- Every opening tag MUST have a matching closing tag, including nested shapes (`<p:nvPr>` must close with `</p:nvPr>`, `<p:nvSpPr>` with `</p:nvSpPr>`, etc.). Double-check before responding.",
        "- Maintain original namespaces, prefixes, and attributes unless a change is explicitly requested. Do not introduce placeholder text such as `...</>` or remove required attributes.",
        "- If only minor edits are needed (e.g., text changes), clone the original XML and apply minimal modifications rather than recreating the entire structure from scratch."
    ])
    
    # Combine all parts
    final_prompt_parts = prompt_context_parts + prompt_data_parts + prompt_instruction_parts
    final_prompt_text = "\n".join(final_prompt_parts)

    print(f"Constructed prompt. Approx. JSON length: {len(json_summary_for_prompt)}, Approx. Slide XMLs length: {slide_xml_chars_total}, Approx. Other XMLs length: {total_other_xml_chars}")
    if (slide_xml_chars_total + total_other_xml_chars) > 400000: 
        print("WARNING: The total XML content is very large and may exceed LLM token limits or be very costly.")
    return final_prompt_text

def get_relevant_xml_files_heuristic(user_prompt: str, ppt_json_data: dict, all_xml_file_paths: list) -> list:
    """Heuristically determine which XML files are likely relevant to the user's request."""
    user_prompt_lower = user_prompt.lower()

    slide_map = {}
    master_map = {}
    layout_map = {}
    rels_map = {}
    theme_path = None
    presentation_xml = None
    presentation_rels = None

    for p in all_xml_file_paths:
        p_posix = Path(p).as_posix()

        m = re.search(r"ppt/slides/slide(\d+)\.xml$", p_posix)
        if m:
            slide_map[int(m.group(1))] = p_posix
        m = re.search(r"ppt/slideMasters/slideMaster(\d+)\.xml$", p_posix)
        if m:
            master_map[m.group(1)] = p_posix
        m = re.search(r"ppt/slideLayouts/slideLayout(\d+)\.xml$", p_posix)
        if m:
            layout_map[m.group(1)] = p_posix
        if re.search(r"ppt/theme/theme\d+\.xml$", p_posix):
            if not theme_path:
                theme_path = p_posix
        if p_posix.endswith("ppt/presentation.xml"):
            presentation_xml = p_posix
        if p_posix.endswith("ppt/_rels/presentation.xml.rels"):
            presentation_rels = p_posix
        if p_posix.endswith(".rels") and "/_rels/" in p_posix:
            parent = p_posix.replace("/_rels/", "/")
            parent = parent[:-5] if parent.endswith(".rels") else parent
            rels_map[parent] = p_posix

    relevant_files = set()

    global_keywords = [
        "all slides",
        "every slide",
        "entire presentation",
        "theme",
        "template",
        "master slide",
        "font style",
        "color scheme",
        "globally",
    ]

    if any(k in user_prompt_lower for k in global_keywords):
        relevant_files.update(master_map.values())
        relevant_files.update(layout_map.values())
        if theme_path:
            relevant_files.add(theme_path)

    slide_nums = [int(n) for n in re.findall(r"(?:slide|page)s?\s*(\d+)", user_prompt_lower)]

    for num in slide_nums:
        if num in slide_map:
            slide_path = slide_map[num]
            relevant_files.add(slide_path)
            if slide_path in rels_map:
                relevant_files.add(rels_map[slide_path])

    if not slide_nums:
        terms = re.findall(r'"([^"]+)"', user_prompt_lower)
        terms += re.findall(r"\b[a-zA-Z]{4,}\b", user_prompt_lower)

        for slide in ppt_json_data.get("slides", []):
            combined_text = " ".join(
                [shape.get("text", "") for shape in slide.get("shapes", [])]
            ).lower()
            combined_text += " " + slide.get("notes", "").lower()
            for term in terms:
                if term in combined_text:
                    num = slide.get("slide_number")
                    if num in slide_map:
                        slide_path = slide_map[num]
                        relevant_files.add(slide_path)
                        if slide_path in rels_map:
                            relevant_files.add(rels_map[slide_path])
                    break

    action_slide_keywords = ["add image", "insert picture", "chart", "diagram", "table"]
    if any(k in user_prompt_lower for k in action_slide_keywords):
        for num in slide_nums:
            slide_path = slide_map.get(num)
            if slide_path and slide_path in rels_map:
                relevant_files.add(rels_map[slide_path])

    struct_keywords = ["add slide", "insert slide", "create slide", "delete slide", "remove slide", "reorder", "move slide"]
    if any(k in user_prompt_lower for k in struct_keywords):
        if presentation_xml:
            relevant_files.add(presentation_xml)
        if presentation_rels:
            relevant_files.add(presentation_rels)

    # Dependency traversal via .rels files
    added = True
    while added:
        added = False
        current_files = list(relevant_files)
        for fpath in current_files:
            rels = rels_map.get(fpath)
            if rels and rels not in relevant_files:
                relevant_files.add(rels)
                added = True
            if fpath.endswith('.rels') and os.path.exists(fpath):
                try:
                    content = open(fpath, 'r', encoding='utf-8').read()
                except Exception:
                    continue
                for m in re.findall(r"slideLayout(\d+)\.xml", content):
                    layout = layout_map.get(m)
                    if layout and layout not in relevant_files:
                        relevant_files.add(layout)
                        added = True
                for m in re.findall(r"slideMaster(\d+)\.xml", content):
                    master = master_map.get(m)
                    if master and master not in relevant_files:
                        relevant_files.add(master)
                        added = True
                theme_match = re.search(r"theme(\d+)\.xml", content)
                if theme_match and theme_path:
                    if theme_path not in relevant_files:
                        relevant_files.add(theme_path)
                        added = True

    if not relevant_files:
        if presentation_xml:
            relevant_files.add(presentation_xml)
        if theme_path:
            relevant_files.add(theme_path)
        relevant_files.update(master_map.values())
        relevant_files.update(slide_map.values())

    return list(relevant_files)

# --- Wrapper functions expected by llm_handler.py ---

def build_xml_editing_prompt(
    user_prompt,
    ppt_json_data,
    xml_file_paths,
    image_inputs=None,
    edit_history=None,
    edit_plan=None
):
    """
    Wrapper to build the main XML editing prompt.
    Returns a list of messages (OpenAI format).
    """
    # Determine if images are present
    image_inputs_present = bool(image_inputs)
    num_slides_with_images = len(image_inputs) if image_inputs else 0
    
    prompt_text = _construct_llm_input_prompt(
        user_prompt=user_prompt,
        ppt_json_data=ppt_json_data,
        xml_file_paths=xml_file_paths,
        image_inputs_present=image_inputs_present,
        num_slides_with_images=num_slides_with_images,
        edit_history=edit_history,
        edit_plan=edit_plan
    )
    
    # Return as a list of messages
    return [
        {"role": "system", "content": "You are an expert PowerPoint XML editor."},
        {"role": "user", "content": prompt_text}
    ]

def build_planning_prompt(user_prompt, ppt_json_data, xml_file_paths):
    """
    Builds a prompt for the planning step.
    """
    json_summary = json.dumps(ppt_json_data, indent=2)
    if len(json_summary) > 50000:
        json_summary = json_summary[:50000] + "... (truncated)"
        
    file_list = "\n".join([Path(p).name for p in xml_file_paths])
    
    prompt = f"""
    You are a technical planner for PowerPoint modifications.
    User Request: "{user_prompt}"
    
    Presentation Structure (JSON Summary):
    {json_summary}
    
    Available XML Files:
    {file_list}
    
    Your task is to identify EXACTLY which XML files need to be modified to fulfill the user's request.
    Return a JSON object with the following structure:
    {{
        "reasoning": "Brief explanation of why these files are selected",
        "targets": ["ppt/slides/slide1.xml", "ppt/theme/theme1.xml", ...]
    }}
    Only include files that strictly need modification.
    """
    return [{"role": "system", "content": "You are a precise planning assistant."}, {"role": "user", "content": prompt}]

def build_judge_prompt(instruction, diff_text, gt_images, pred_images):
    """
    Builds a prompt for the LLM judge.
    """
    prompt = f"""
    You are an expert judge evaluating an AI's ability to modify PowerPoint presentations.
    
    Instruction: {instruction}
    
    Structural Differences (JSON Diff):
    {diff_text}
    
    Visual Comparison:
    (Images would be provided here in a real multimodal context)
    
    Evaluate the modification based on:
    1. Instruction Following: Did the AI do exactly what was asked?
    2. Visual Quality: Is the result visually correct and broken?
    
    Return a JSON object:
    {{
        "instruction_following_score": (0-10),
        "visual_quality_score": (0-10),
        "instruction_following_reason": "...",
        "visual_quality_reason": "..."
    }}
    """
    
    messages = [{"role": "system", "content": "You are a strict and fair judge."}, {"role": "user", "content": prompt}]
    
    # If images are provided, we would add them to the user message content in a multimodal format.
    # For now, we assume text-only prompt construction here or handle images in the caller if needed.
    # The caller (llm_handler) handles image attachments if using a multimodal model.
    
    return messages
