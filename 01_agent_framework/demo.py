"""Runs a few sample requests through the Agent and prints each trace."""

from agent import Agent

REQUESTS = [
    "How many days of annual leave do I get?",
    "My laptop screen is broken, can you log a ticket for IT?",
    "What's the policy on working remote full time?",
    "What's the capital of France?",  # no tool matches -- direct answer
]

agent = Agent()
for req in REQUESTS:
    result = agent.run(req)
    print(f"\n> {req}")
    print(f"  answer: {result['answer']}")
    print("  trace:")
    for step in result["trace"]:
        print(f"    - {step}")

print(f"\n{len(REQUESTS)} requests run -- see tickets.json for any tickets raised.")
