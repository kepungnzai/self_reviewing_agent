"""
agent.py

A multi-agent "software delivery" pipeline built with Google ADK.

    intake  ->  LoopAgent[ architect -> designer -> coder -> reviewer ]  ->  release_manager

The task is deliberately trivial (App on AWS, Azure or GCP) so the
interesting part is the *process*: the reviewer is built to REASSESS the
architect's decisions from first principles, not merely proofread the code.
Reassessment is made real by three things:

  1. A shared, auditable decision log (log_decision tool). Every major decision
     is PROPOSED, then judged KEEP / REVISE / REVERSE, then REVISED / REVERSED /
     RETAINED by the architect on the next pass.
  2. A deterministic exit gate (approve_and_exit tool). The LLM cannot end the
     loop by being "in a good mood": score must be >= APPROVAL_THRESHOLD and at
     least MIN_ITERATIONS passes must have happened.
  3. The architect is instructed to be willing to reverse itself.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Optional

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import ToolContext
from google.genai import types

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
MODEL = os.getenv("ADK_MODEL", "gemini-3.5-flash")
REVIEWER_MODEL = os.getenv("ADK_REVIEWER_MODEL", MODEL)  # try a stronger model here

MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))  # hard stop for the loop
MIN_ITERATIONS = 2  # a first draft is never approved outright
APPROVAL_THRESHOLD = 8  # reviewer score (1-10) required to exit

SUPPORTED_CLOUDS = ("aws", "azure", "gcp")
_CLOUD_ALIASES = {
    "amazon": "aws",
    "amazon web services": "aws",
    "microsoft": "azure",
    "microsoft azure": "azure",
    "google": "gcp",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
}
DECISION_STATUSES = {
    # architect
    "PROPOSED", "REVISED", "REVERSED", "RETAINED",
    # reviewer
    "KEEP", "REVISE", "REVERSE",
}

assert MIN_ITERATIONS <= MAX_ITERATIONS, "MIN_ITERATIONS must be <= MAX_ITERATIONS"

_GEN_CONFIG = types.GenerateContentConfig(temperature=0.3)


# --------------------------------------------------------------------------- #
# Tools (all shared state lives in session state via tool_context.state)
# --------------------------------------------------------------------------- #
def set_target_cloud(cloud: str, assumed: bool, tool_context: ToolContext) -> dict:
    """Record which cloud provider the deployment targets and initialise run state.

    Args:
        cloud: One of "aws", "azure" or "gcp".
        assumed: True if the user did not name a provider and a default was chosen.
    """
    key = cloud.strip().lower()
    key = _CLOUD_ALIASES.get(key, key)
    if key not in SUPPORTED_CLOUDS:
        return {"status": "error", "message": f"cloud must be one of {SUPPORTED_CLOUDS}"}

    s = tool_context.state
    s["target_cloud"] = key
    s["cloud_assumed"] = assumed
    s["iteration"] = 0
    s["approved"] = False
    s["decision_log"] = []
    s["decision_log_text"] = "(no decisions yet)"
    s["files"] = {}
    s["files_text"] = "(no code yet)"
    return {"status": "ok", "target_cloud": key}


def log_decision(decision: str, status: str, rationale: str, tool_context: ToolContext) -> dict:
    """Append one entry to the shared decision log.

    Args:
        decision: Short statement of the decision, e.g. "Use Cloud Run instead of GKE".
        status: Architect uses PROPOSED, REVISED, REVERSED or RETAINED.
            Reviewer uses KEEP, REVISE or REVERSE.
        rationale: Why. For REVISE/REVERSE/REVISED/REVERSED say what changed the mind.
    """
    status = status.strip().upper()
    if status not in DECISION_STATUSES:
        return {"status": "error", "message": f"status must be one of {sorted(DECISION_STATUSES)}"}

    s = tool_context.state
    log = list(s.get("decision_log", []))
    log.append(
        {
            "iteration": s.get("iteration", 0),
            "author": tool_context.agent_name,
            "decision": decision,
            "status": status,
            "rationale": rationale,
        }
    )
    s["decision_log"] = log
    s["decision_log_text"] = "\n".join(
        f"[iter {e['iteration']}] {e['author']:<9} {e['status']:<9} {e['decision']} -- {e['rationale']}"
        for e in log
    )
    return {"status": "logged", "entries": len(log)}


def save_file(filename: str, content: str, tool_context: ToolContext) -> dict:
    """Save one generated file (relative path, e.g. "app/main.py" or "infra/main.tf").

    Args:
        filename: Relative POSIX path. No absolute paths, no "..".
        content: Complete file contents.
    """
    p = PurePosixPath(filename)
    if p.is_absolute() or ".." in p.parts or not p.name:
        return {"status": "error", "message": "filename must be a safe relative path"}

    s = tool_context.state
    files = dict(s.get("files", {}))
    files[str(p)] = content
    s["files"] = files
    s["files_text"] = "\n\n".join(f"### FILE: {n}\n```\n{c}\n```" for n, c in files.items())
    return {"status": "saved", "filename": str(p), "total_files": len(files)}


def approve_and_exit(score: int, justification: str, tool_context: ToolContext) -> dict:
    """Approve the current iteration and end the refinement loop.

    Call ONLY when every major decision is KEEP and design, code and ADR are consistent.
    The gate is enforced in code: low scores and too-early approvals are refused.

    Args:
        score: Overall quality score from 1 (poor) to 10 (excellent).
        justification: One or two sentences explaining the approval.
    """
    s = tool_context.state
    iteration = s.get("iteration", 1)

    if score < APPROVAL_THRESHOLD:
        return {"status": "refused", "reason": f"Score {score} is below {APPROVAL_THRESHOLD}. Request changes instead."}
    if iteration < MIN_ITERATIONS:
        return {
            "status": "refused",
            "reason": (
                "A first-pass design is never approved outright. Identify at least one decision that "
                "could be simpler, safer or cheaper, log it as REVISE or REVERSE, and request changes. "
                "If you truly find none, log your strongest rejected alternative and why it lost, then "
                "request one improvement to the weakest artifact."
            ),
        }

    s["approved"] = True
    s["approval_score"] = score
    s["approval_justification"] = justification
    tool_context.actions.escalate = True  # <- this is what terminates the LoopAgent
    return {"status": "approved", "iteration": iteration, "score": score}


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
def _start_iteration(callback_context: CallbackContext) -> Optional[types.Content]:
    """Runs before the architect each pass; bumps the iteration counter."""
    st = callback_context.state
    st["iteration"] = st.get("iteration", 0) + 1
    return None


def _reset_files(callback_context: CallbackContext) -> Optional[types.Content]:
    """The coder rewrites the full file set every pass, so stale files never linger."""
    st = callback_context.state
    st["files"] = {}
    st["files_text"] = "(no code yet)"
    return None


# --------------------------------------------------------------------------- #
# Instructions
# NOTE: ADK treats {name} in an instruction as a session-state placeholder and
# {name?} as optional. Do not use literal curly braces in these strings.
# --------------------------------------------------------------------------- #
INTAKE_INSTRUCTION = """\
You are the intake analyst for the targeted cloud delivery pipeline.

Read the user's request and decide the deployment target: aws, azure or gcp.
- Call set_target_cloud exactly once. Set assumed=true only if the user named no provider.
- If the user named no provider, choose gcp and say it was a defaulted assumption.
- If several providers are named, pick the first and say the rest are out of scope.

Then reply with a requirements statement of 4-6 lines covering: target cloud (and whether
assumed), goal, language (Python 3.12), and constraints (lowest cost at zero traffic, minimal operational
burden, infrastructure-as-code, easy teardown).
"""

ARCHITECT_INSTRUCTION = """\
You are the ARCHITECT in the targeted delivery pipeline.

Target cloud: {target_cloud}
Requirements:
{requirements}

Current iteration: {iteration}

Current ADR (empty on the first pass):
{adr?}

Design from the previous iteration, including its ADR concerns (empty on the first pass):
{design?}

Decision log so far:
{decision_log_text}

Your job
1. First pass: choose the hosting pattern and the infrastructure-as-code tool for this cloud.
   Evaluate at least three alternatives (for example serverless function, managed container
   service, VM, Kubernetes) and reject each with a concrete reason.
2. Later passes: answer every item in the reviewer feedback. Be genuinely willing to REVERSE an
   earlier decision when the reviewer's argument is stronger than yours. Never defend a
   decision just because you made it.
3. Call log_decision once per major decision. First pass: status PROPOSED. Later passes:
   REVISED, REVERSED or RETAINED, and in the rationale name the reviewer item that caused
   the change (or why you held your ground).
4. Finish with the ADR in markdown with sections: Context, Decision, Alternatives considered,
   Consequences, Open risks. Output only the ADR.
"""

DESIGNER_INSTRUCTION = """\
You are the DESIGNER. Turn the ADR into a buildable design.

Target cloud: {target_cloud}
ADR:
{adr}

Reviewer feedback from the previous iteration (empty on the first pass):
{design?}

Produce a markdown design with these sections:
- Resource inventory: table of every cloud resource (name, type, purpose, why it is needed)
- Request flow: step by step from client to response
- Identity and permissions: each identity with its exact permissions, least privilege
- File layout: every file the coder must write, one line each
- Test plan: how to verify locally and after deploy
- Cost at zero traffic, and teardown steps
- ADR concerns: anything in the ADR that turned out to be wrong, unnecessary or underspecified
  once you worked through the details. Write "none" only if that is true.

Do not add resources the ADR does not justify. If the reviewer assigned items to the designer,
address them explicitly.
"""

CODER_INSTRUCTION = """\
You are the CODER. Implement the design exactly.

Target cloud: {target_cloud}
ADR:
{adr}

Design:
{design}

Reviewer feedback from the previous iteration (empty on the first pass):
{review_feedback?}

Rules
- Save every file with the save_file tool, one call per file. Earlier files were cleared, so
  write the COMPLETE set every pass.
- Required files: the Python 3.12 application code (standard library only unless the design
  demands otherwise), offline unit tests, the infrastructure-as-code named in the ADR, and a
  README.md with deploy, verify and teardown commands.
- The service must return exactly the text Hello, World!
- Pin versions, no secrets in code, no wildcard permissions.
- If the code cannot follow the design, do not deviate silently: list every deviation.

Finish with a short manifest: file names, then a Deviations from design list (or "none").
"""

REVIEWER_INSTRUCTION = """\
You are the REVIEWER: an independent skeptic whose job is to REASSESS DECISIONS, not to polish
code. Do not rubber-stamp.

Target cloud: {target_cloud}
Iteration: {iteration}

ADR:
{adr}

Design:
{design}

Code:
{files_text}

Decision log so far:
{decision_log_text}

Procedure
1. Reassess every major decision in the ADR from first principles. Ask: "If I started today with
   only the requirements, would I choose this?" Apply these lenses: simplicity (smallest thing
   that satisfies the requirement), cost at zero traffic, security (public exposure, least
   privilege), operability (deploy, teardown, logs) and cloud-native fit. Before you KEEP a
   decision, state the strongest alternative and why it loses.
2. Check consistency: does the design implement the ADR, and does the code implement the design?
   Flag any resource in code that is not in the design, or in the design but not in the ADR.
   Flag defects: wrong output text, missing tests, wildcard permissions, unpinned versions.
3. Call log_decision once per major decision with status KEEP, REVISE or REVERSE and a rationale.
4. Decide.
   - If any decision is REVISE or REVERSE, or you found an inconsistency or defect: do NOT approve.
     Reply "VERDICT: CHANGES REQUESTED" followed by at most 5 numbered, specific, actionable
     items. Tag each with its owner: [architect], [designer] or [coder]. Only request changes
     that materially improve correctness, security, cost or simplicity; ignore style.
   - If every decision is KEEP and everything is consistent: call approve_and_exit with a score
     from 1 to 10 and a justification. If the tool refuses, follow its message and request changes.
     After a successful approval reply "VERDICT: APPROVED" and one sentence.
Keep the reply under 300 words.
"""

RELEASE_INSTRUCTION = """\
You are the RELEASE MANAGER. Write the final delivery note for the user from the state below.
Do not invent facts that are not in it.

Target cloud: {target_cloud} (assumed by default: {cloud_assumed})
Approved by reviewer: {approved}
Iterations used: {iteration}

Final ADR:
{adr}

Decision log:
{decision_log_text}

Last reviewer feedback:
{review_feedback?}

Write, in under 250 words:
1. Outcome: one paragraph. If Approved is False, say the loop ran out of iterations and list the
   open reviewer items.
2. What reassessment changed: every decision whose log shows REVISE, REVERSE, REVISED or
   REVERSED, with before and after. If nothing changed, say so plainly.
3. Final architecture in at most five lines.
4. Next step: point to the README.md in the generated files for deploy and teardown.
"""

# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #
intake_agent = LlmAgent(
    name="intake",
    model=MODEL,
    description="Determines the target cloud (aws/azure/gcp) and states requirements.",
    instruction=INTAKE_INSTRUCTION,
    tools=[set_target_cloud],
    output_key="requirements",
    generate_content_config=_GEN_CONFIG,
)

architect_agent = LlmAgent(
    name="architect",
    model=MODEL,
    description="Chooses the architecture, weighs alternatives, and revises when challenged.",
    instruction=ARCHITECT_INSTRUCTION,
    tools=[log_decision],
    output_key="adr",
    before_agent_callback=_start_iteration,
    generate_content_config=_GEN_CONFIG,
)

designer_agent = LlmAgent(
    name="designer",
    model=MODEL,
    description="Turns the ADR into a resource-level design and flags ADR concerns.",
    instruction=DESIGNER_INSTRUCTION,
    output_key="design",
    generate_content_config=_GEN_CONFIG,
)

coder_agent = LlmAgent(
    name="coder",
    model=MODEL,
    description="Writes app code, tests, IaC and README from the design.",
    instruction=CODER_INSTRUCTION,
    tools=[save_file],
    output_key="code_manifest",
    before_agent_callback=_reset_files,
    generate_content_config=_GEN_CONFIG,
)

reviewer_agent = LlmAgent(
    name="reviewer",
    model=REVIEWER_MODEL,
    description="Reassesses architectural decisions and consistency; gates loop exit.",
    instruction=REVIEWER_INSTRUCTION,
    tools=[log_decision, approve_and_exit],
    output_key="review_feedback",
    generate_content_config=_GEN_CONFIG,
)

refinement_loop = LoopAgent(
    name="refinement_loop",
    description="Architect, design, code, then reassess. Repeats until approved or MAX_ITERATIONS.",
    #sub_agents=[architect_agent, designer_agent, coder_agent, reviewer_agent],
    sub_agents=[architect_agent, coder_agent, reviewer_agent],
    max_iterations=MAX_ITERATIONS,
)

release_manager_agent = LlmAgent(
    name="release_manager",
    model=MODEL,
    description="Summarises the outcome and what reassessment changed.",
    instruction=RELEASE_INSTRUCTION,
    generate_content_config=_GEN_CONFIG,
)

root_agent = SequentialAgent(
    name="hello_world_factory",
    description="Delivers a hello-world service on AWS, Azure or GCP with reassessed decisions.",
    sub_agents=[intake_agent, refinement_loop, release_manager_agent],
)