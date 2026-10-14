"""Agents propose. People decide. Only then does anything run.

The agent's only power is `propose()`, which records a step and returns. Nothing executes until a human
calls `approve()`, and an approval is tied to the exact tool and arguments that were shown to them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class Status(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalError(Exception): ...
class UnknownTool(ApprovalError): ...
class InvalidArguments(ApprovalError): ...
class StepTampered(ApprovalError): ...


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable[..., Any]
    risk: Risk
    description: str = ""
    validate: Callable[[dict], None] | None = None  # raise ValueError for bad arguments


class ToolRegistry:
    """The allow-list. An agent can only propose tools registered here."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, fn: Callable[..., Any], risk: Risk, description: str = "",
                 validate: Callable[[dict], None] | None = None) -> None:
        self._tools[name] = Tool(name, fn, risk, description, validate)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise UnknownTool(f"tool {name!r} is not registered")
        return self._tools[name]


@dataclass
class Step:
    id: int
    tool: str
    args: dict
    rationale: str
    proposed_by: str
    risk: Risk
    fingerprint: str
    proposed_at: float
    status: Status = Status.PROPOSED
    decided_by: str | None = None
    decided_at: float | None = None
    reason: str | None = None
    result: Any = None
    error: str | None = None

    def preview(self) -> str:
        """What the approver sees: exactly what will run, and why the agent wants it."""
        return f"#{self.id} [{self.risk.value.upper()}] {self.tool}({json.dumps(self.args, sort_keys=True)})  because: {self.rationale}"


def _fingerprint(tool: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, default=str).encode()).hexdigest()


class Runner:
    def __init__(self, registry: ToolRegistry, *, approval_ttl: float = 900.0,
                 clock: Callable[[], float] = time.time, log_path: str | None = None,
                 allow_self_approval: bool = False, approvers: set[str] | None = None) -> None:
        self.registry, self.ttl, self._clock = registry, approval_ttl, clock
        self.log_path, self.allow_self_approval, self.approvers = log_path, allow_self_approval, approvers
        self.steps: dict[int, Step] = {}
        self.events: list[dict] = []

    # ---- what an agent can do ----------------------------------------------------------------------
    def propose(self, agent: str, tool: str, args: dict, rationale: str = "") -> Step:
        """Record a step for review. Never executes anything."""
        t = self.registry.get(tool)
        args = copy.deepcopy(args)  # the agent keeps no handle on what the human approves
        if t.validate:
            try:
                t.validate(args)
            except ValueError as e:
                raise InvalidArguments(str(e)) from e
        step = Step(len(self.steps) + 1, tool, args, rationale, agent, t.risk, _fingerprint(tool, args), self._clock())
        self.steps[step.id] = step
        self._log("proposed", step, by=agent)
        return step

    # ---- what a human can do -----------------------------------------------------------------------
    def pending(self) -> list[Step]:
        return [s for s in self.steps.values() if s.status is Status.PROPOSED]

    def approve(self, step_id: int, approver: str) -> Step:
        step = self._decidable(step_id, approver)
        step.status, step.decided_by, step.decided_at = Status.APPROVED, approver, self._clock()
        self._log("approved", step, by=approver)
        return step

    def reject(self, step_id: int, approver: str, reason: str = "") -> Step:
        step = self._decidable(step_id, approver)
        step.status, step.decided_by, step.decided_at, step.reason = Status.REJECTED, approver, self._clock(), reason
        self._log("rejected", step, by=approver)
        return step

    def _decidable(self, step_id: int, approver: str) -> Step:
        step = self._get(step_id)
        if step.status is not Status.PROPOSED:
            raise ApprovalError(f"step {step_id} is {step.status.value}, not awaiting a decision")
        if self.approvers is not None and approver not in self.approvers:
            raise ApprovalError(f"{approver!r} is not an authorised approver")
        if approver == step.proposed_by and not self.allow_self_approval:
            raise ApprovalError("a step cannot be approved by the agent that proposed it")
        self._check_fingerprint(step)
        return step

    # ---- execution (only ever of approved steps) ---------------------------------------------------
    def execute(self, step_id: int) -> Step:
        step = self._get(step_id)
        if step.status is not Status.APPROVED:
            raise ApprovalError(f"step {step_id} is {step.status.value}; only approved steps can run")
        if self._clock() - (step.decided_at or 0) > self.ttl:
            step.status, step.reason = Status.REJECTED, "approval expired"
            self._log("expired", step, by="system")
            raise ApprovalError(f"approval for step {step_id} expired; propose it again")
        self._check_fingerprint(step)
        step.status = Status.EXECUTED  # set first: an approval is single-use even if the tool crashes
        try:
            step.result = self.registry.get(step.tool).fn(**copy.deepcopy(step.args))
            self._log("executed", step, by="runner")
        except Exception as e:  # noqa: BLE001 - tool errors are recorded, not hidden
            step.status, step.error = Status.FAILED, f"{type(e).__name__}: {e}"
            self._log("failed", step, by="runner")
        return step

    def run_approved(self) -> list[Step]:
        """Run every approved step in order; stop at the first failure."""
        done = []
        for s in [s for s in self.steps.values() if s.status is Status.APPROVED]:
            try:
                done.append(self.execute(s.id))
            except ApprovalError:
                continue
            if done[-1].status is Status.FAILED:
                break
        return done

    # ---- internals ---------------------------------------------------------------------------------
    def _get(self, step_id: int) -> Step:
        if step_id not in self.steps:
            raise ApprovalError(f"no such step: {step_id}")
        return self.steps[step_id]

    def _check_fingerprint(self, step: Step) -> None:
        if _fingerprint(step.tool, step.args) != step.fingerprint:
            self._log("tamper_detected", step, by="system")
            raise StepTampered(f"step {step.id} was changed after it was proposed")

    def _log(self, event: str, step: Step, by: str) -> None:
        rec = {"ts": self._clock(), "event": event, "step": step.id, "tool": step.tool, "risk": step.risk.value,
               "by": by, "args": step.args, "reason": step.reason, "error": step.error}
        self.events.append(rec)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
