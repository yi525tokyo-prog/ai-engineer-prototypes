"""Runs a handful of representative tasks through the router and prints
where each one landed and why."""

from router import route_and_run

TASKS = [
    "Refactor this function to remove the nested loop and add a unit test.",
    "Draft an email to a customer explaining a shipping delay.",
    "Summarize this incident report into 3 bullet points for leadership.",
    "Write a regex to validate NZ phone numbers.",
    "What's our policy on international travel expenses?",
]

for task in TASKS:
    entry = route_and_run(task)
    print(f"\n> {task}")
    print(f"  lane: {entry['lane']}  ({entry['reason']})")
    print(f"  output: {entry['output']}")

print(f"\n{len(TASKS)} tasks routed -- see routing_log.jsonl for the audit trail.")
