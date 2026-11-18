"""
LAYER 4 - ADK's own evaluator (tool-trajectory + LLM-judge rubrics) on the agents where it fits.

ADK evals run ONE agent against an evalset.  intake_eval/ and release_eval/ expose the intake
and release agents as root_agent so they can be evaluated alone (see README for why not the
whole pipeline).  Each evals/<name>/ folder holds <name>.evalset.json + test_config.json;
AgentEvaluator reads test_config.json from the same folder as the evalset.

    pytest -m llm tests/test_adk_native.py
    adk eval intake_eval evals/intake/intake.evalset.json --config_file_path=evals/intake/test_config.json --print_detailed_results
"""
import pathlib

import pytest
from google.adk.evaluation.agent_evaluator import AgentEvaluator

pytestmark = pytest.mark.llm
ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module,dataset", [("intake_eval", "evals/intake"),
                                            ("release_eval", "evals/release")])
async def test_adk_native_eval(module, dataset):
    # If your ADK version exposes a synchronous evaluate(), remove the await.
    await AgentEvaluator.evaluate(agent_module=module,
                                  eval_dataset_file_path_or_dir=str(ROOT / dataset),
                                  num_runs=2)
