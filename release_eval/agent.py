"""Exposes ONLY the release manager as root_agent; evalsets seed its session state."""
from agent import release_manager_agent

root_agent = release_manager_agent.model_copy(update={"parent_agent": None})
