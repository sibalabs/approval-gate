<div align="center">

# approval-gate

**Agents propose. People decide. Only then does anything run.**

![python](https://img.shields.io/badge/python-3.10%2B-blue) ![deps](https://img.shields.io/badge/dependencies-0-brightgreen) ![tests](https://img.shields.io/badge/tests-13%20passing-brightgreen) ![license](https://img.shields.io/badge/license-MIT-lightgrey)

</div>

---

## The problem

An AI agent that can send emails, change records or call APIs is useful, and also one confident mistake away from doing something you can't undo. "Be careful" in a prompt isn't a control.

**approval-gate makes the control structural.** The agent's only power is to *propose* a step. The step sits in a queue, shown to a person exactly as it would run. Nothing executes until that person approves it.

```
 agent ──propose()──► [ queue: proposed ] ──► person reviews ──┬─ approve() ──► execute()  (once, before it expires)
                                                               └─ reject()  ──► never runs
                      every transition ──► JSON-lines audit log
```

## Quick start

```bash
pip install -e .
python examples/demo.py                 # scripted reviewer
python examples/demo.py --interactive   # you approve each step
```

```python
from approval_gate import Runner, ToolRegistry, Risk

reg = ToolRegistry()
reg.register("send_email", lambda to, body: send(to, body), Risk.WRITE)

runner = Runner(reg, log_path="audit.jsonl")

step = runner.propose("agent", "send_email", {"to": "buyer@example.com", "body": "Reorder 40 units"},
                      rationale="SKU-17 is below minimum")   # nothing has run
print(step.preview())            # #1 [WRITE] send_email({...})  because: SKU-17 is below minimum

runner.approve(step.id, "dee")   # a person decides
runner.run_approved()            # now it runs, once
```

## Guarantees (and the tests that prove them)

| Guarantee | Test |
|---|---|
| Proposing never executes anything | `test_proposing_never_executes` |
| Unapproved and rejected steps can't run | `test_unapproved_step_cannot_run`, `test_rejected_step_never_runs` |
| An approval is single-use | `test_approved_step_runs_once` |
| The agent can't approve its own step | `test_agent_cannot_approve_its_own_step` |
| Optional allow-list of who may approve | `test_only_listed_approvers_when_configured` |
| Only registered tools; arguments validated up front | `test_unknown_tools_and_bad_arguments...` |
| The approver sees exactly what runs: the agent keeps no live handle on the arguments | `test_agent_mutating_its_dict...` |
| Editing a step after approval is detected and blocked (SHA-256 fingerprint) | `test_editing_a_step_after_approval...` |
| Approvals expire (default 15 min), so stale yeses don't linger | `test_approval_expires` |
| A failing tool is recorded and halts the steps behind it | `test_tool_failure_is_recorded...` |
| Complete history in an append-only JSONL file | `test_full_history_is_logged_to_a_file` |

## API

| Call | Who | What it does |
|---|---|---|
| `ToolRegistry.register(name, fn, risk, description, validate)` | developer | Allow-list a tool with a `Risk` (`READ`, `WRITE`, `DESTRUCTIVE`) and optional argument validator |
| `Runner.propose(agent, tool, args, rationale)` | agent | Queue a step. Raises `UnknownTool` / `InvalidArguments` |
| `Runner.pending()` | human | Steps awaiting a decision |
| `Step.preview()` | human | One-line description of exactly what would run and why |
| `Runner.approve(id, approver)` / `reject(id, approver, reason)` | human | Decide. Final. |
| `Runner.execute(id)` / `run_approved()` | system | Run approved steps (in order; stops at first failure) |

Options: `approval_ttl`, `approvers={"dee"}`, `allow_self_approval=False`, `log_path`, `clock` (for tests).

## Design decisions

- **Strict by default.** Even `READ` steps need approval. If you want reads to run freely, wrap that policy yourself; the safe behaviour shouldn't be something you have to remember to switch on.
- **Approval is bound to the exact action.** It's a fingerprint of tool plus arguments, so "approve this email" can't turn into "approve a different email".
- **Mark executed *before* running.** If a tool crashes halfway, the approval is already spent and can't be replayed.
- **Errors are data.** A failed tool doesn't crash the runner; it's logged and the queue stops.
- **Zero dependencies.** Works with any agent framework: it only needs the model's output to become `propose()` calls.

## Limits (read these)

- This is the control layer, not a UI. In production, `approve()` sits behind a login and a screen or chat message that shows `preview()`.
- State is in memory (plus the log file). For a real deployment, persist steps in a database so approvals survive restarts.
- It guarantees the *approved* action runs, not that the human read it carefully. Keep previews short and specific.
- Tools themselves must be safe to call with approved arguments; the gate decides *whether*, not *how*.

## Project layout

```
src/approval_gate/runner.py   Runner, ToolRegistry, Step (~190 lines, the whole implementation)
tests/test_runner.py          13 tests
examples/demo.py              an agent proposes three steps; a reviewer approves two, rejects the destructive one
```

## Development

```bash
pip install -e ".[dev]" && pytest
```

---

Built by [Siba Labs](https://www.upwork.com/). All example data is invented. MIT licensed.
