"""Shared helpers for tests that call a live model through ADK's Runner."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

OUT_DIR = Path("eval_out")  # every live run is dumped here for audit / golden-fixture capture


@dataclass
class RunResult:
    state: dict
    final_text: str
    tool_calls: list   # [{"author", "name", "args"}]
    tool_results: list  # [{"author", "name", "response"}]
    total_tokens: int
    seconds: float


def _detach(agent):
    """An agent that is already a sub_agent has a parent; run a parent-less copy as root."""
    if getattr(agent, "parent_agent", None) is not None:
        return agent.model_copy(update={"parent_agent": None})
    return agent


async def run_agent(agent, prompt: str, *, state: dict | None = None,
                    app_name: str = "eval_app", user_id: str = "eval_user") -> RunResult:
    svc = InMemorySessionService()
    runner = Runner(agent=_detach(agent), app_name=app_name, session_service=svc)
    session = await svc.create_session(app_name=app_name, user_id=user_id, state=dict(state or {}))
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])

    calls, results, tokens, final_text = [], [], 0, ""
    t0 = time.monotonic()
    async for ev in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg):
        for fc in ev.get_function_calls():
            calls.append({"author": ev.author, "name": fc.name, "args": dict(fc.args or {})})
        for fr in ev.get_function_responses():
            results.append({"author": ev.author, "name": fr.name, "response": dict(fr.response or {})})
        if ev.usage_metadata and ev.usage_metadata.total_token_count:
            tokens += ev.usage_metadata.total_token_count
        if ev.is_final_response() and ev.content and ev.content.parts:
            text = "".join(p.text or "" for p in ev.content.parts if getattr(p, "text", None))
            if text:
                final_text = text  # last final response wins (release_manager in the full pipeline)
    seconds = time.monotonic() - t0

    session = await svc.get_session(app_name=app_name, user_id=user_id, session_id=session.id)
    return RunResult(dict(session.state), final_text, calls, results, tokens, seconds)


def dump_run(name: str, res: RunResult) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", name)
    (OUT_DIR / f"{safe}.json").write_text(json.dumps(asdict(res), indent=2, default=str), encoding="utf-8")


def assert_pass_rate(failures_per_run: list, required: int) -> None:
    """failures_per_run[i] is the list of problems found in run i ([] = run passed)."""
    passed = sum(1 for f in failures_per_run if not f)
    assert passed >= required, (
        f"{passed}/{len(failures_per_run)} runs passed (need {required}). "
        f"Problems per run: {failures_per_run}")
