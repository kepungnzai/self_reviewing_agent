"""
LAYER 1 - deterministic unit tests.  No LLM, no network, no credentials, milliseconds.
Run on every commit.  These protect the parts of the pipeline that are CODE, not prompts:
the tools, the exit gate, the callbacks, the state wiring.
"""
import re

import pytest

#from agent import agent as A
import agent as A

# --------------------------------------------------------------------------- #
# Minimal stand-in for ADK's ToolContext / CallbackContext
# --------------------------------------------------------------------------- #
class _Actions:
    def __init__(self):
        self.escalate = False


class Ctx:
    def __init__(self, agent_name="reviewer", **state):
        self.state = dict(state)
        self.agent_name = agent_name
        self.actions = _Actions()


def _entry(iteration, author, status, decision="d", rationale="r"):
    return {"iteration": iteration, "author": author, "decision": decision,
            "status": status, "rationale": rationale}


def _ready_to_approve(iteration=2):
    """State in which approve_and_exit SHOULD be allowed (reviewer logged KEEP, code exists)."""
    return Ctx(
        "reviewer",
        iteration=iteration,
        decision_log=[_entry(iteration, "reviewer", "KEEP")],
        files={"app/main.py": "x"},
    )


# --------------------------------------------------------------------------- #
# set_target_cloud  (intake -> everything downstream depends on this state)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("aws", "aws"), ("  AWS ", "aws"), ("Amazon Web Services", "aws"),
    ("azure", "azure"), ("Microsoft Azure", "azure"),
    ("GCP", "gcp"), ("Google Cloud", "gcp"), ("google cloud platform", "gcp"),
])
def test_set_target_cloud_normalises_aliases(raw, expected):
    ctx = Ctx("intake")
    assert A.set_target_cloud(raw, False, ctx) == {"status": "ok", "target_cloud": expected}
    assert ctx.state["target_cloud"] == expected


@pytest.mark.parametrize("raw", ["oracle", "alibaba", "", "kubernetes", "aws; rm -rf /"])
def test_set_target_cloud_rejects_unsupported(raw):
    ctx = Ctx("intake")
    assert A.set_target_cloud(raw, False, ctx)["status"] == "error"
    assert "target_cloud" not in ctx.state  # nothing half-initialised


def test_set_target_cloud_resets_previous_run_state():
    ctx = Ctx("intake", iteration=5, approved=True, decision_log=[{"x": 1}],
              files={"a": "b"}, files_text="old", decision_log_text="old")
    A.set_target_cloud("gcp", True, ctx)
    s = ctx.state
    assert (s["iteration"], s["approved"], s["decision_log"], s["files"]) == (0, False, [], {})
    assert s["cloud_assumed"] is True
    assert s["decision_log_text"] == "(no decisions yet)" and s["files_text"] == "(no code yet)"


# --------------------------------------------------------------------------- #
# log_decision  (the audit trail the release note is built from)
# --------------------------------------------------------------------------- #
def test_log_decision_records_author_iteration_and_normalises_status():
    ctx = Ctx("architect", iteration=2)
    assert A.log_decision("Use Cloud Run", "revised", "why", ctx) == {"status": "logged", "entries": 1}
    assert ctx.state["decision_log"][0] == {
        "iteration": 2, "author": "architect", "decision": "Use Cloud Run",
        "status": "REVISED", "rationale": "why"}


def test_log_decision_rejects_unknown_status():
    ctx = Ctx("architect")
    assert A.log_decision("x", "MAYBE", "y", ctx)["status"] == "error"
    assert "decision_log" not in ctx.state


def test_log_decision_appends_and_renders_text():
    ctx = Ctx("architect", iteration=1)
    A.log_decision("A", "PROPOSED", "r1", ctx)
    ctx.agent_name = "reviewer"
    A.log_decision("A", "KEEP", "r2", ctx)
    lines = ctx.state["decision_log_text"].splitlines()
    assert len(ctx.state["decision_log"]) == 2 and len(lines) == 2
    assert "PROPOSED" in lines[0] and lines[1].startswith("[iter 1] reviewer") and "KEEP" in lines[1]


# --------------------------------------------------------------------------- #
# save_file  (writes LLM-chosen paths -> must be safe)
# --------------------------------------------------------------------------- #
def test_save_file_stores_and_renders():
    ctx = Ctx("coder")
    assert A.save_file("app/main.py", "print(1)", ctx) == {
        "status": "saved", "filename": "app/main.py", "total_files": 1}
    assert "### FILE: app/main.py" in ctx.state["files_text"]


def test_save_file_overwrites_same_name():
    ctx = Ctx("coder")
    A.save_file("a.py", "v1", ctx)
    assert A.save_file("a.py", "v2", ctx)["total_files"] == 1
    assert ctx.state["files"]["a.py"] == "v2"


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x.py", "a/../../b", "", "dir/.."])
def test_save_file_rejects_unsafe_posix_paths(bad):
    ctx = Ctx("coder")
    assert A.save_file(bad, "x", ctx)["status"] == "error"
    assert "files" not in ctx.state


# --------------------------------------------------------------------------- #
# approve_and_exit  (THE deterministic gate - the claim "the LLM cannot end the loop")
# --------------------------------------------------------------------------- #
def test_approve_refuses_score_below_threshold():
    ctx = _ready_to_approve(2)
    assert A.approve_and_exit(A.APPROVAL_THRESHOLD - 1, "j", ctx)["status"] == "refused"
    assert not ctx.actions.escalate and ctx.state.get("approved") is not True


def test_approve_refuses_before_min_iterations():
    ctx = _ready_to_approve(A.MIN_ITERATIONS - 1)
    assert A.approve_and_exit(10, "j", ctx)["status"] == "refused"
    assert not ctx.actions.escalate and ctx.state.get("approved") is not True


def test_approve_refuses_when_iteration_unknown():
    ctx = Ctx("reviewer", decision_log=[_entry(1, "reviewer", "KEEP")], files={"a": "b"})
    assert A.approve_and_exit(10, "j", ctx)["status"] == "refused"  # defaults to iteration 1


def test_approve_succeeds_exactly_at_threshold_and_min_iteration():
    ctx = _ready_to_approve(A.MIN_ITERATIONS)
    out = A.approve_and_exit(A.APPROVAL_THRESHOLD, "solid", ctx)
    assert out == {"status": "approved", "iteration": A.MIN_ITERATIONS, "score": A.APPROVAL_THRESHOLD}
    s = ctx.state
    assert s["approved"] is True and s["approval_score"] == A.APPROVAL_THRESHOLD
    assert s["approval_justification"] == "solid"
    assert ctx.actions.escalate is True  # this flag is what terminates the LoopAgent


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
def test_start_iteration_increments_from_zero_or_missing():
    ctx = Ctx()
    assert A._start_iteration(ctx) is None and ctx.state["iteration"] == 1
    A._start_iteration(ctx)
    assert ctx.state["iteration"] == 2


def test_reset_files_clears_stale_files():
    ctx = Ctx(files={"a": "b"}, files_text="x")
    A._reset_files(ctx)
    assert ctx.state["files"] == {} and ctx.state["files_text"] == "(no code yet)"


# --------------------------------------------------------------------------- #
# Wiring / configuration  (static checks that catch prompt-vs-code drift)
# --------------------------------------------------------------------------- #
def _tool_names(agent):
    return {getattr(t, "__name__", getattr(t, "name", str(t))) for t in (agent.tools or [])}


PIPELINE = [A.intake_agent, A.architect_agent, A.designer_agent,
            A.coder_agent, A.reviewer_agent, A.release_manager_agent]
_PH = re.compile(r"\{(\w+)(\?)?\}")


def _after_intake_keys():
    ctx = Ctx("intake")
    A.set_target_cloud("gcp", True, ctx)
    return set(ctx.state) | {A.intake_agent.output_key}


ALL_KEYS = _after_intake_keys() | {a.output_key for a in PIPELINE if getattr(a, "output_key", None)}


def test_loop_budget_allows_approval():
    assert A.MIN_ITERATIONS <= A.MAX_ITERATIONS
    assert 1 <= A.APPROVAL_THRESHOLD <= 10


def test_pipeline_order_and_loop_bound():
    assert [a.name for a in A.root_agent.sub_agents] == ["intake", "refinement_loop", "release_manager"]
    assert [a.name for a in A.refinement_loop.sub_agents] == ["architect", "designer", "coder", "reviewer"]
    assert A.refinement_loop.max_iterations == A.MAX_ITERATIONS


def test_only_reviewer_can_end_the_loop():
    for ag in PIPELINE:
        assert ("approve_and_exit" in _tool_names(ag)) == (ag.name == "reviewer"), ag.name


def test_tool_least_privilege():
    assert _tool_names(A.intake_agent) == {"set_target_cloud"}
    assert _tool_names(A.architect_agent) == {"log_decision"}
    assert _tool_names(A.designer_agent) == set()
    assert _tool_names(A.coder_agent) == {"save_file"}
    assert _tool_names(A.reviewer_agent) == {"log_decision", "approve_and_exit"}
    assert _tool_names(A.release_manager_agent) == set()


@pytest.mark.parametrize("idx", range(1, len(PIPELINE)))
def test_required_placeholders_exist_when_agent_runs(idx):
    """ADK raises at runtime if a required {key} is missing from session state."""
    agent = PIPELINE[idx]
    available = _after_intake_keys() | {a.output_key for a in PIPELINE[1:idx] if a.output_key}
    for name, optional in _PH.findall(agent.instruction):
        if not optional:
            assert name in available, f"{agent.name}: required {{{name}}} is not in state yet"


def test_every_placeholder_is_a_known_state_key():
    """Catches typos such as {review_feedbak?} that would silently stay empty."""
    for ag in PIPELINE:
        for name, _ in _PH.findall(ag.instruction):
            assert name in ALL_KEYS, f"{ag.name}: unknown placeholder {{{name}}}"


# =========================================================================== #
# HARDENING TESTS - these describe gaps in the CURRENT agent.py.
# They fail today; apply the "Hardening patch" in README.md and they go green.
# =========================================================================== #
@pytest.mark.hardening
@pytest.mark.parametrize("bad", ["..\\evil.py", "C:\\x.py", "C:/x.py", "\\\\server\\share\\x"])
def test_H1_save_file_rejects_windows_style_paths(bad):
    # PurePosixPath treats backslashes and drive letters as ordinary characters.
    ctx = Ctx("coder")
    assert A.save_file(bad, "x", ctx)["status"] == "error"


@pytest.mark.hardening
@pytest.mark.parametrize("score", [0, -1, 11, 100])
def test_H2_out_of_range_score_is_refused(score):
    ctx = _ready_to_approve(2)
    assert A.approve_and_exit(score, "j", ctx)["status"] == "refused"
    assert not ctx.actions.escalate


@pytest.mark.hardening
def test_H3_cannot_approve_while_a_revise_or_reverse_is_logged_this_iteration():
    ctx = _ready_to_approve(2)
    ctx.state["decision_log"].append(_entry(2, "reviewer", "REVERSE", "Use X"))
    assert A.approve_and_exit(9, "j", ctx)["status"] == "refused"
    assert not ctx.actions.escalate


@pytest.mark.hardening
def test_H3b_cannot_approve_without_logging_any_reviewer_decision():
    ctx = _ready_to_approve(2)
    ctx.state["decision_log"] = []
    assert A.approve_and_exit(9, "j", ctx)["status"] == "refused"


@pytest.mark.hardening
def test_H4_cannot_approve_when_no_files_were_saved():
    ctx = _ready_to_approve(2)
    ctx.state["files"] = {}
    assert A.approve_and_exit(9, "j", ctx)["status"] == "refused"


@pytest.mark.hardening
@pytest.mark.parametrize("agent_name,status", [
    ("reviewer", "PROPOSED"), ("reviewer", "REVISED"), ("architect", "KEEP"), ("architect", "REVERSE")])
def test_H5_roles_cannot_log_each_others_statuses(agent_name, status):
    ctx = Ctx(agent_name, iteration=1)
    assert A.log_decision("d", status, "r", ctx)["status"] == "error"
