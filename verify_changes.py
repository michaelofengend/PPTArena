import sys
import os
from pathlib import Path

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

# Mock missing dependencies
from unittest.mock import MagicMock
sys.modules["openai"] = MagicMock()
sys.modules["google"] = MagicMock()
sys.modules["google.generativeai"] = MagicMock()
sys.modules["google.generativeai.types"] = MagicMock()
sys.modules["PIL"] = MagicMock()
sys.modules["PIL.Image"] = MagicMock()
sys.modules["numpy"] = MagicMock()
sys.modules["cv2"] = MagicMock()
sys.modules["pptx"] = MagicMock()
sys.modules["pptx.enum"] = MagicMock()
sys.modules["pptx.enum.shapes"] = MagicMock()
sys.modules["pptx.enum.text"] = MagicMock()
sys.modules["pptx.util"] = MagicMock()
sys.modules["pptx.dml"] = MagicMock()
sys.modules["pptx.presentation"] = MagicMock()
sys.modules["pptx.oxml"] = MagicMock()
sys.modules["pptx.oxml.xmlchemy"] = MagicMock()
sys.modules["pptx.oxml.simpletypes"] = MagicMock()
sys.modules["pptx.oxml.ns"] = MagicMock()
sys.modules["ppt"] = MagicMock()
sys.modules["ppt.ppt_processor"] = MagicMock()

# Mock internal imports that might fail if dependencies are missing
# We need to make sure llm_handler can import these. 
# If llm.utils imports openai, we need to mock it there too, but sys.modules should handle it.

try:
    import llm_handler
except ImportError as e:
    print(f"Failed to import llm_handler: {e}")
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred during import: {e}")
    sys.exit(1)

def test_router_selection():
    print("Testing Router Selection...")
    
    # Mock _log to capture output
    original_log = llm_handler._log
    logs = []
    def mock_log(msg, req_id=None):
        logs.append(msg)
        # original_log(msg, req_id) # Optional: print to stdout too

    llm_handler._log = mock_log

    # Test Case 1: OpenAI Preference
    print("  Case 1: User prefers 'gpt-4o'")
    # We expect the router to force 'gpt-5-nano-2025-08-07'
    # Note: call_llm_router might try to make an API call. 
    # We want to verify the LOG message before the API call fails (or succeeds).
    # The log happens right at the start of the function.
    
    # To avoid actual API calls, we can mock _create_openai_client and _configure_gemini_client
    # or just catch the error, but we only care about the log message "Calling LLM Router..."
    
    try:
        llm_handler.call_llm_router("test prompt", {}, preferred_model_id="gpt-4o")
    except Exception:
        pass # Expected failure due to missing keys or mock

    found_nano = any("gpt-5-nano-2025-08-07" in log for log in logs)
    if found_nano:
        print("    PASS: Router selected gpt-5-nano-2025-08-07 for OpenAI preference.")
    else:
        print("    FAIL: Router did NOT select gpt-5-nano-2025-08-07.")
        print("    Logs:", logs)

    # Clear logs
    logs.clear()

    # Test Case 2: Gemini Preference
    print("  Case 2: User prefers 'gemini-1.5-pro'")
    try:
        llm_handler.call_llm_router("test prompt", {}, preferred_model_id="gemini-1.5-pro")
    except Exception:
        pass

    found_flash = any("gemini-2.5-flash" in log for log in logs)
    if found_flash:
        print("    PASS: Router selected gemini-2.5-flash for Gemini preference.")
    else:
        print("    FAIL: Router did NOT select gemini-2.5-flash.")
        print("    Logs:", logs)

    # Restore log
    llm_handler._log = original_log

def test_planner_selection():
    print("\nTesting Planner Selection...")
    
    original_log = llm_handler._log
    logs = []
    def mock_log(msg, req_id=None):
        logs.append(msg)

    llm_handler._log = mock_log

    # Test Case 1: OpenAI Model
    print("  Case 1: Planning for 'gpt-4o'")
    try:
        llm_handler.plan_xml_edits_with_router("test", {}, [], model_id="gpt-4o")
    except Exception:
        pass

    found_nano = any("gpt-5-nano-2025-08-07" in log for log in logs)
    if found_nano:
        print("    PASS: Planner selected gpt-5-nano-2025-08-07 for OpenAI model.")
    else:
        print("    FAIL: Planner did NOT select gpt-5-nano-2025-08-07.")
        print("    Logs:", logs)

    logs.clear()

    # Test Case 2: Gemini Model
    print("  Case 2: Planning for 'gemini-1.5-pro'")
    try:
        llm_handler.plan_xml_edits_with_router("test", {}, [], model_id="gemini-1.5-pro")
    except Exception:
        pass

    found_flash = any("gemini-2.5-flash" in log for log in logs)
    if found_flash:
        print("    PASS: Planner selected gemini-2.5-flash for Gemini model.")
    else:
        print("    FAIL: Planner did NOT select gemini-2.5-flash.")
        print("    Logs:", logs)

    llm_handler._log = original_log

if __name__ == "__main__":
    test_router_selection()
    test_planner_selection()
