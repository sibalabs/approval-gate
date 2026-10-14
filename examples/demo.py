"""Run: python examples/demo.py  (add --interactive to approve each step yourself)"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
from approval_gate import Risk, Runner, ToolRegistry

outbox, records = [], ["old-1", "old-2"]
reg = ToolRegistry()
reg.register("read_report", lambda name: f"contents of {name}", Risk.READ)
reg.register("send_email", lambda to, body: outbox.append((to, body)) or "queued", Risk.WRITE)
reg.register("delete_records", lambda ids: [records.remove(i) for i in ids] and "deleted", Risk.DESTRUCTIVE)

runner = Runner(reg)

# The "agent": in real use, a model decides these. It can only propose.
runner.propose("agent", "read_report", {"name": "weekly_stock.csv"}, "need current stock levels")
runner.propose("agent", "send_email", {"to": "buyer@example.com", "body": "Reorder 40 units of SKU-17"}, "SKU-17 is below minimum")
runner.propose("agent", "delete_records", {"ids": ["old-1", "old-2"]}, "tidy up old records")

print("Nothing has run yet. outbox =", outbox, "records =", records, "\n")
for step in runner.pending():
    print(step.preview())
    if "--interactive" in sys.argv:
        yes = input("  approve? [y/N] ").strip().lower() == "y"
    else:
        yes = step.risk is not Risk.DESTRUCTIVE  # scripted human: approves all but the destructive one
    runner.approve(step.id, "dee") if yes else runner.reject(step.id, "dee", "not now")
    print("  ->", "APPROVED" if yes else "REJECTED")

print("\nRunning approved steps...")
for s in runner.run_approved():
    print(f"  #{s.id} {s.tool}: {s.status.value} {s.result!r}")
print("\noutbox =", outbox, "records =", records)
