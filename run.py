#!/usr/bin/env python3
"""
run.py - programmatic runner for the hello_factory pipeline.

    python run.py "Hello world on AWS"
    python run.py "I want a hello world on azure" --out ./generated
    python run.py                       # no cloud named -> intake defaults to gcp

Streams each agent's activity, marks every loop iteration, then writes the
final artifacts (code, ADR, design, decision log) to <out>/<cloud>/.

Auth (pick one):
    export GOOGLE_API_KEY=...                                   # Google AI Studio
    export GOOGLE_GENAI_USE_VERTEXAI=TRUE GOOGLE_CLOUD_PROJECT=... GOOGLE_CLOUD_LOCATION=...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:  # ADK already depends on python-dotenv; load .env if present
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import MAX_ITERATIONS, root_agent

APP_NAME = "hello_factory"
USER_ID = "local-user"


def _clip(text: str, max_lines: int) -> str:
    lines = text.strip().splitlines()
    if max_lines <= 0 or len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines; use --verbose)"


def _indent(text: str, pad: str = "    ") -> str:
    return "\n".join(pad + ln for ln in text.splitlines())


def _fmt_call(name: str, args: dict) -> str:
    if name == "log_decision":
        return f"log_decision [{args.get('status')}] {args.get('decision')}"
    if name == "save_file":
        return f"save_file {args.get('filename')}"
    if name == "approve_and_exit":
        return f"approve_and_exit score={args.get('score')}"
    if name == "set_target_cloud":
        return f"set_target_cloud {args.get('cloud')} (assumed={args.get('assumed')})"
    return f"{name}({args})"


async def run(prompt: str, out_dir: Path, verbose: bool) -> int:
    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)

    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    max_lines = 0 if verbose else 14

    print(f"Prompt: {prompt}\n")
    last_author: str | None = None
    iteration = 0
    final_note = ""

    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        author = event.author

        # The architect is always first in a pass, so a fresh architect turn == new iteration.
        if author == "architect" and last_author != "architect":
            iteration += 1
            print("=" * 72)
            print(f" ITERATION {iteration} of max {MAX_ITERATIONS}")
            print("=" * 72)
        last_author = author

        if not (event.content and event.content.parts):
            continue

        for part in event.content.parts:
            if part.function_call:
                print(f"  [{author}] tool> {_fmt_call(part.function_call.name, dict(part.function_call.args or {}))}")
            elif part.function_response:
                resp = part.function_response.response or {}
                if resp.get("status") in {"error", "refused"}:
                    print(f"  [{author}] tool< {resp.get('status').upper()}: {resp.get('reason') or resp.get('message')}")
            elif part.text and event.is_final_response():
                print(f"\n  [{author}]")
                print(_indent(_clip(part.text, max_lines)))
                print()
                if author == "release_manager":
                    final_note = part.text

    # ---- pull final state and persist artifacts ----
    session = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    state = session.state
    cloud = state.get("target_cloud", "unknown")
    approved = bool(state.get("approved"))
    target = out_dir / cloud
    target.mkdir(parents=True, exist_ok=True)

    for rel, content in state.get("files", {}).items():
        dest = (target / rel).resolve()
        if target.resolve() not in dest.parents:  # belt and braces vs path traversal
            print(f"skipped unsafe path: {rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    docs = target / "_docs"
    docs.mkdir(exist_ok=True)
    (docs / "ADR.md").write_text(state.get("adr", ""), encoding="utf-8")
    (docs / "DESIGN.md").write_text(state.get("design", ""), encoding="utf-8")
    (docs / "DECISION_LOG.md").write_text(state.get("decision_log_text", ""), encoding="utf-8")
    (docs / "LAST_REVIEW.md").write_text(state.get("review_feedback", ""), encoding="utf-8")

    print("=" * 72)
    print(" DECISION LOG (what was proposed, challenged and changed)")
    print("=" * 72)
    for e in state.get("decision_log", []):
        print(f"  iter {e['iteration']} | {e['author']:<9} | {e['status']:<8} | {e['decision']}")
        print(f"          why: {e['rationale']}")

    print()
    print(f"Cloud      : {cloud}{' (defaulted)' if state.get('cloud_assumed') else ''}")
    print(f"Iterations : {state.get('iteration', 0)}")
    print(f"Approved   : {approved}" + (f" (score {state.get('approval_score')})" if approved else " (loop hit max iterations)"))
    print(f"Artifacts  : {target}")
    return 0 if approved else 2


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the application design, architecture, implementation and self improvement process.")
    ap.add_argument("prompt", nargs="?", default="Build me a hello world service.", help="Free-text request; name aws, azure or gcp.")
    ap.add_argument("--out", default="./generated", help="Output directory (default ./generated)")
    ap.add_argument("--verbose", action="store_true", help="Print full agent outputs")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.prompt, Path(args.out), args.verbose)))


if __name__ == "__main__":
    main()