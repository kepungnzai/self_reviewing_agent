"""
LAYER 3 - the whole pipeline, live model, invariant checks.

You cannot assert the exact text an LLM pipeline produces, but you CAN assert properties that
must hold for every run: the right cloud, the loop obeyed its bounds, the exit gate was honoured,
the decision log is well formed, required files exist, "Hello, World!" is in the code, no obvious
secrets or admin roles, the release note is short, and cost/latency stayed inside a budget.

Every run is dumped to eval_out/<case>.json - open it to see exactly what the agents did.

    pytest -m e2e tests/test_pipeline_e2e.py
Calibrate the budgets after your first runs:  E2E_TOKEN_BUDGET, E2E_TIME_BUDGET_S
"""
import os
import re
from pathlib import PurePosixPath

import pytest

from agent import (APPROVAL_THRESHOLD, DECISION_STATUSES, MAX_ITERATIONS,
                                       MIN_ITERATIONS, SUPPORTED_CLOUDS, root_agent)
from tests.harness import dump_run, run_agent

pytestmark = pytest.mark.e2e

TOKEN_BUDGET = int(os.getenv("E2E_TOKEN_BUDGET", "600000"))
TIME_BUDGET_S = int(os.getenv("E2E_TIME_BUDGET_S", "1200"))
IAC_EXT = {".tf", ".tfvars", ".bicep", ".yaml", ".yml", ".json"}
ADR_HEADINGS = ["Context", "Decision", "Alternatives considered", "Consequences", "Open risks"]
SECRET_PATTERNS = [r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{35}",
                   r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                   r"(?i)(password|secret|api[_-]?key)\s*[:=]\s*[\"'][^\"']{8,}[\"']"]
OVERBROAD_PATTERNS = [r"(?i)\"?Action\"?\s*[:=]\s*\[?\s*\"\*\"", r"(?i)roles/(owner|editor)\b",
                      r"AdministratorAccess", r"(?i)role_definition_name\s*=\s*\"(Owner|Contributor)\""]


def check_run(res, expect_cloud=None, expect_assumed=None) -> list:
    p, s = [], res.state
    cloud = s.get("target_cloud")
    if cloud not in SUPPORTED_CLOUDS:
        return [f"target_cloud is {cloud!r}, not one of {SUPPORTED_CLOUDS}"]
    if expect_cloud and cloud != expect_cloud:
        p.append(f"target_cloud={cloud}, expected {expect_cloud}")
    if expect_assumed is not None and s.get("cloud_assumed") is not expect_assumed:
        p.append(f"cloud_assumed={s.get('cloud_assumed')}, expected {expect_assumed}")

    # --- tool discipline -------------------------------------------------------------------
    if len([c for c in res.tool_calls if c["name"] == "set_target_cloud"]) != 1:
        p.append("set_target_cloud must be called exactly once")
    if any(c["name"] == "approve_and_exit" and c["author"] != "reviewer" for c in res.tool_calls):
        p.append("a non-reviewer agent called approve_and_exit")

    # --- loop bounds and gate ----------------------------------------------------------------
    it, approved = s.get("iteration", 0), s.get("approved") is True
    if not 1 <= it <= MAX_ITERATIONS:
        p.append(f"iteration={it} outside 1..{MAX_ITERATIONS}")
    if approved:
        if it < MIN_ITERATIONS:
            p.append("approved before MIN_ITERATIONS")
        if s.get("approval_score", 0) < APPROVAL_THRESHOLD:
            p.append("approved below threshold")
    elif it != MAX_ITERATIONS:
        p.append("loop stopped without approval before MAX_ITERATIONS")

    # --- produced files ----------------------------------------------------------------------
    files = s.get("files") or {}
    base = lambda n: PurePosixPath(n).name.lower()          # noqa: E731
    app = [n for n in files if n.endswith(".py") and "test" not in base(n)]
    if not any(base(n) == "readme.md" for n in files):
        p.append("no README.md")
    if not app:
        p.append("no application .py file")
    if not any("test" in base(n) for n in files):
        p.append("no test file")
    if not any(PurePosixPath(n).suffix.lower() in IAC_EXT for n in files):
        p.append("no infrastructure-as-code file")
    if app and not any("Hello, World!" in files[n] for n in app):
        p.append('exact text "Hello, World!" not found in application code')
    for n, c in files.items():
        if any(re.search(x, c) for x in SECRET_PATTERNS):
            p.append(f"possible hard-coded secret in {n}")
        if any(re.search(x, c) for x in OVERBROAD_PATTERNS):
            p.append(f"over-broad permission in {n}")
        if base(n).startswith("requirements") and n.endswith(".txt"):
            p += [f"unpinned requirement in {n}: {ln}" for ln in c.splitlines()
                  if ln.strip() and not ln.startswith("#") and "==" not in ln]

    # --- decision log ----------------------------------------------------------------------
    log = s.get("decision_log") or []
    arch = [e for e in log if e["author"] == "architect"]
    rev = [e for e in log if e["author"] == "reviewer"]
    p += [f"invalid status {e['status']}" for e in log if e["status"] not in DECISION_STATUSES]
    p += ["architect iter 1 entry is not PROPOSED" for e in arch if e["iteration"] == 1 and e["status"] != "PROPOSED"]
    p += ["architect later entry is not REVISED/REVERSED/RETAINED" for e in arch
          if e["iteration"] > 1 and e["status"] not in {"REVISED", "REVERSED", "RETAINED"}]
    for k in range(1, it + 1):
        if not any(e["iteration"] == k for e in rev):
            p.append(f"reviewer logged nothing in iteration {k}")
        if k > 1 and not any(e["iteration"] == k for e in arch):
            p.append(f"architect did not answer the review in iteration {k}")

    # --- format contracts between agents ---------------------------------------------------
    adr = s.get("adr", "")
    p += [f"ADR missing section '{h}'" for h in ADR_HEADINGS
          if not re.search(rf"^#+\s*{h}", adr, re.I | re.M)]
    if approved != ("VERDICT: APPROVED" in s.get("review_feedback", "").upper()):
        p.append("reviewer's text verdict disagrees with the code gate")

    # --- release note, cost, latency ---------------------------------------------------------
    if len(res.final_text.split()) > 300:
        p.append("release note longer than ~250 words")
    if "README" not in res.final_text.upper():
        p.append("release note does not point to the README")
    if res.total_tokens > TOKEN_BUDGET:
        p.append(f"tokens {res.total_tokens} > budget {TOKEN_BUDGET}")
    if res.seconds > TIME_BUDGET_S:
        p.append(f"{res.seconds:.0f}s > budget {TIME_BUDGET_S}s")
    return p


CASES = [
    pytest.param("Build a hello world web service.", "gcp", True, id="no_provider_defaults_to_gcp"),
    pytest.param("Deploy a hello world service on AWS.", "aws", False, id="explicit_aws"),
    pytest.param("I need hello world running on Azure.", "azure", False, id="explicit_azure"),
    pytest.param("Hello world on AWS and also GCP please.", "aws", False, id="multi_provider_takes_first"),
]


@pytest.mark.parametrize("prompt,cloud,assumed", CASES)
async def test_pipeline_invariants(prompt, cloud, assumed, request):
    res = await run_agent(root_agent, prompt, app_name="hello_world_factory")
    dump_run(request.node.name, res)
    problems = check_run(res, cloud, assumed)
    assert not problems, "\n".join(problems)


async def test_unsupported_provider_degrades_gracefully(request):
    """The intake prompt has no rule for providers outside aws/azure/gcp. If set_target_cloud
    never succeeds, downstream agents fail with 'context variable not found'. This test pins
    whatever behaviour you decide on (default to gcp, or stop with a clear message)."""
    res = await run_agent(root_agent, "Deploy a hello world service on Oracle Cloud.",
                          app_name="hello_world_factory")
    dump_run(request.node.name, res)
    assert res.state.get("target_cloud") in SUPPORTED_CLOUDS
