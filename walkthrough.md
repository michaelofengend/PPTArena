# PPTPilot Refactoring Walkthrough

This document outlines the changes made to the PPTPilot codebase to improve modularity and remove the `tiktoken` dependency.

## Changes Overview

1.  **Modularization**: The monolithic `src/llm_handler.py` and `src/ppt_processor.py` (now split) have been refactored into smaller, focused modules within `src/llm/` and `src/ppt/`.
2.  **Tiktoken Removal**: The `tiktoken` library has been removed from `requirements.txt` and all source code. Token counting is now handled via API response metadata where available, or simply logged without local counting.
3.  **New Directory Structure**:
    *   `src/llm/`: Contains LLM-related logic.
        *   `utils.py`: API key loading, client creation, logging.
        *   `prompt.py`: Prompt construction logic.
    *   `src/ppt/`: Contains PowerPoint processing logic.
        *   `analysis.py`: JSON conversion, diffing.
        *   `xml_handler.py`: XML extraction and modification.
        *   `export.py`: Image and PDF export.
        *   `validation.py`: XML validation and repair.
        *   `utils.py`: General utilities.
    *   `src/utils/`: General utilities.
        *   `image_utils.py`: Image processing helpers.
    *   `src/work_dir/`: New consolidated directory for all data output (uploads, sessions, generated files).

## Verification

The refactored code has been verified by:
1.  **Static Analysis**: Checking for `tiktoken` usage (none found).
2.  **Import Verification**: Installing dependencies in a temporary environment and successfully importing all main modules (`app`, `orchestrator`, `llm_handler`).

## How to Run

1.  Navigate to the `GithupCopy` directory.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Run the Flask app:
    ```bash
    python src/app.py
    ```

## Key Files Modified/Created

*   `src/app.py`: Updated imports to use new modules and consolidated data folders into `src/work_dir/`.
*   `src/orchestrator.py`: Updated imports to use new modules and consolidated data folders into `src/work_dir/`.
*   `src/llm_handler.py`: Refactored to use `src/llm/` and `src/ppt/` modules.
*   `src/llm/utils.py`: New file.
*   `src/llm/prompt.py`: New file.
*   `src/ppt/*.py`: New files replacing `ppt_processor.py`.
*   `requirements.txt`: Removed `tiktoken`.
