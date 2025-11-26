# PPTPilot

PPTPilot is a web-based tool for evaluating and editing PowerPoint presentations using Large Language Models (LLMs). It allows you to compare "Original" vs "Ground Truth" presentations and generate new versions based on natural language prompts.

## Directory Structure

- `src/`: Contains the source code for the Flask application and helper modules.
- `Original/`: Contains the original PowerPoint files used for evaluation.
- `GroundTruth/`: Contains the ground truth PowerPoint files for comparison.
- `evaluation_pairs_refined.json`: Metadata defining the evaluation cases (pairs of Original and Ground Truth files).
- `25_sample.json`: Additional sample data.
- `requirements.txt`: Python dependencies.
- `credentials.env`: Configuration file for API keys.

## Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

## Installation

1.  Navigate to the project directory:
    ```bash
    cd .
    ```

2.  Install the required dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

1.  Open `credentials.env` in a text editor.
2.  Add your API keys for the LLMs you intend to use. The file expects the following format:
    ```env
    OPENAI_API_KEY=your_openai_api_key_here
    GEMINI_API_KEY=your_gemini_api_key_here
    ```
    (Note: `OPENAI_ORG_ID` is optional).

## Running the Application

1.  Run the Flask application:
    ```bash
    python src/app.py
    ```

2.  Open your web browser and navigate to:
    ```
    http://localhost:5000
    ```

## Usage

- **Evaluation Tab**: Select a test case from the dropdown to view the "Original" and "Ground Truth" side-by-side. You can generate a prediction using an LLM and compare it against the Ground Truth.
- **Chat Tab**: Upload a PowerPoint file and use the chat interface to request edits using natural language.

## Troubleshooting

- If you encounter issues with missing directories (e.g., `sessions`, `generated_pdfs`), ensure you have write permissions in the `src` directory, as the app attempts to create them.
- If API calls fail, double-check your `credentials.env` file and ensure your API keys are valid.
