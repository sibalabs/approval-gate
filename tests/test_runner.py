import json

import pytest

from approval_gate import (ApprovalError, InvalidArguments, Risk, Runner, Status, StepTampered, ToolRegistry,
                           UnknownTool)


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


@pytest.fixture
def env():
    sent, clock = [], Clock()
    reg = ToolRegistry()
    reg.register("send_email", lambda to, body: sent.append((to, body)) or "sent", Risk.WRITE,
                 validate=lambda a: None if "@" in a.get("to", "") else (_ for _ in ()).throw(ValueError("bad address")))
    reg.register("boom", lambda: 1 / 0, Risk.WRITE)
    reg.register("ok", lambda: "fine", Risk.READ)
    return Runner(reg, approval_ttl=60, clock=clock), sent, clock


def test_proposing_never_executes(env):
    r, sent, _ = env
    s = r.propose("agent", "send_email", {"to": "a@x.com", "body": "hi"}, "customer asked")
    assert s.status is Status.PROPOSED and sent == []


def test_unapproved_step_cannot_run(env):
    r, sent, _ = env
    s = r.propose("agent", "send_email", {"to": "a@x.com", "body": "hi"})
    with pytest.raises(ApprovalError, match="only approved"):
        r.execute(s.id)
    assert sent == [] and r.run_approved() == []


def test_approved_step_runs_once(env):
    r, sent, _ = env
    s = r.propose("agent", "send_email", {"to": "a@x.com", "body": "hi"})
    r.approve(s.id, "dee")
    assert r.execute(s.id).result == "sent" and sent == [("a@x.com", "hi")]
    with pytest.raises(ApprovalError):
        r.execute(s.id)  # single-use
    assert len(sent) == 1


def test_rejected_step_never_runs(env):
    r, sent, _ = env
    s = r.propose("agent", "send_email", {"to": "a@x.com", "body": "hi"})
    r.reject(s.id, "dee", "wrong customer")
    assert s.reason == "wrong customer"
    with pytest.raises(ApprovalError):
        r.execute(s.id)
    with pytest.raises(ApprovalError):
        r.approve(s.id, "dee")  # a decision is final


def test_agent_cannot_approve_its_own_step(env):
    r, _, _ = env
    s = r.propose("agent", "ok", {})
    with pytest.raises(ApprovalError, match="proposed it"):
        r.approve(s.id, "agent")


def test_only_listed_approvers_when_configured(env):
    r, _, clock = env
    r2 = Runner(r.registry, approvers={"dee"}, clock=clock)
    s = r2.propose("agent", "ok", {})
    with pytest.raises(ApprovalError, match="authorised"):
        r2.approve(s.id, "random")
    r2.approve(s.id, "dee")


def test_unknown_tools_and_bad_arguments_are_refused_at_proposal(env):
    r, _, _ = env
    with pytest.raises(UnknownTool):
        r.propose("agent", "rm_rf", {})
    with pytest.raises(InvalidArguments):
        r.propose("agent", "send_email", {"to": "nope", "body": "x"})
    assert r.steps == {}


def test_agent_mutating_its_dict_after_proposing_changes_nothing(env):
    r, sent, _ = env
    args = {"to": "a@x.com", "body": "hi"}
    s = r.propose("agent", "send_email", args)
    args["to"] = "evil@x.com"
    r.approve(s.id, "dee"); r.execute(s.id)
    assert sent == [("a@x.com", "hi")]


def test_editing_a_step_after_approval_is_detected_and_blocked(env):
    r, sent, _ = env
    s = r.propose("agent", "send_email", {"to": "a@x.com", "body": "hi"})
    r.approve(s.id, "dee")
    s.args["to"] = "evil@x.com"
    with pytest.raises(StepTampered):
        r.execute(s.id)
    assert sent == [] and r.events[-1]["event"] == "tamper_detected"


def test_approval_expires(env):
    r, sent, clock = env
    s = r.propose("agent", "ok", {})
    r.approve(s.id, "dee")
    clock.t += 61
    with pytest.raises(ApprovalError, match="expired"):
        r.execute(s.id)
    assert s.status is Status.REJECTED


def test_tool_failure_is_recorded_and_halts_the_rest(env):
    r, _, _ = env
    a, b = r.propose("agent", "boom", {}), r.propose("agent", "ok", {})
    r.approve(a.id, "dee"); r.approve(b.id, "dee")
    done = r.run_approved()
    assert [s.status for s in done] == [Status.FAILED] and "ZeroDivisionError" in a.error
    assert b.status is Status.APPROVED  # still waiting; nothing ran behind a failure


def test_full_history_is_logged_to_a_file(tmp_path):
    reg = ToolRegistry(); reg.register("ok", lambda: 1, Risk.READ)
    p = tmp_path / "log.jsonl"
    r = Runner(reg, log_path=str(p))
    s = r.propose("agent", "ok", {}, "why"); r.approve(s.id, "dee"); r.execute(s.id)
    assert [json.loads(l)["event"] for l in p.read_text().splitlines()] == ["proposed", "approved", "executed"]


def test_preview_shows_what_will_run(env):
    r, _, _ = env
    s = r.propose("agent", "send_email", {"to": "a@x.com", "body": "hi"}, "customer asked")
    assert "[WRITE] send_email" in s.preview() and "customer asked" in s.preview()
