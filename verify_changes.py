
import sys
import os
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent / "src"))

try:
    print("Importing app...")
    import app
    print("Importing orchestrator...")
    import orchestrator
    print("Importing llm_handler...")
    import llm_handler
    print("Importing llm.utils...")
    import llm.utils

    print("Checking load_api_keys structure...")
    # Mock credentials file if it doesn't exist or just check the function
    keys = llm.utils.load_api_keys()
    print(f"Loaded keys: {keys}")
    
    # Check if keys are in expected format (even if empty)
    # We expect keys like 'openai', 'gemini', 'openai_org_id' if they exist in env
    
    print("Verification successful: Modules imported and load_api_keys called.")

except Exception as e:
    print(f"Verification failed: {e}")
    sys.exit(1)
