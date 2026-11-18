"""Exposes ONLY the intake agent as root_agent so `adk eval` can score it in isolation."""
from agent import intake_agent

# A sub_agent already has a parent; run a parent-less copy as the root.
root_agent = intake_agent.model_copy(update={"parent_agent": None})
