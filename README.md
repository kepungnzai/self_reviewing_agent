



$env:GOOGLE_GENAI_USE_VERTEXAI="TRUE"
$env:GOOGLE_CLOUD_PROJECT="project-f54eacb9-b4e5-40b9-b0c"
$env:GOOGLE_CLOUD_LOCATION="global"

Test-Path loop_agent/evals/intake/intake.evalset.json

uv run adk eval loop_agent loop_agent/evals/intake/intake.evalset.json --config_file_path=loop_agent/evals/intake/test_config.json --print_detailed_results

