import json
import subprocess
import concurrent.futures

def run_claude(case):
    original_file = case.get("original")
    prompt = case.get("prompt")
    case_name = case.get("name")
    
    # Construct the instruction for Claude
    instruction = f"Edit the file '{original_file}' according to these instructions: {prompt}"
    
    print(f"Starting: {case_name}")
    try:
        # Run Claude Code in non-interactive mode
        result = subprocess.run(
            ["claude", "-p", instruction],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✅ Success: {case_name}")
        # Uncomment the line below if you want to see Claude's exact output
        # print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {case_name}")
        print(f"Error output: {e.stderr}")
        print(f"Standard output: {e.stdout}")

if __name__ == "__main__":
    # Load your JSON file
    with open("src/25_sample.json", "r") as f:
        cases = json.load(f)
    
    # Run them concurrently (e.g., 5 at a time to respect rate limits)
    max_workers = 5  
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(run_claude, cases)
