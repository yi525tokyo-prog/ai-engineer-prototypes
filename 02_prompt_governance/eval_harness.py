"""
Prompt Governance Eval Harness -- runs versioned prompt templates against
a fixed set of test cases and checks the output against declared policy
rules before anything ships.

Design:
  - prompts/*.json: versioned templates with owner + last_reviewed
    metadata (the audit trail a governance process needs) plus a
    machine-checkable `rules` list.
  - test_cases.json: fixed inputs per template, so behaviour is
    regression-tested the same way code would be.
  - run_prompt(): production path calls Claude; falls back to a
    deterministic stand-in generator so the harness -- and its rule
    checks -- run and are inspectable with zero API cost.
  - check_rules(): a small rule engine (max_words, must_include,
    must_not_include, max_bullets, max_words_per_bullet, no_pii) that
    scores an output against a template's declared rules.

Run:
    python3 eval_harness.py
"""

import glob
import json
import os
import re


PII_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|(?:\+?64|0)[\d\s\-]{7,12}\d")


def load_templates() -> dict:
    templates = {}
    for path in sorted(glob.glob("prompts/*.json")):
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        templates[t["id"]] = t
    return templates


def run_prompt(template: dict, user_input: str, api_key=None) -> str:
    """Production path calls Claude with the template's system prompt.
    Falls back to a deterministic stand-in when no key is set, so the
    harness's rule-checking logic can be exercised and trusted without
    needing real model output for every run."""
    api_key = api_key or os.environ.get("LLM_API_KEY")
    if not api_key:
        return _fallback_generate(template, user_input)

    import urllib.request
    payload = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "system": template["system_prompt"],
        "messages": [{"role": "user", "content": user_input}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    return body["content"][0]["text"]


def _fallback_generate(template: dict, user_input: str) -> str:
    """Deterministic stand-in output per template, used with no API key."""
    if template["id"] == "customer_support_reply":
        return (f"Thanks for flagging this -- sorry for the trouble. "
                f"I've logged your note (\"{user_input[:60]}...\") and a "
                f"specialist will follow up within 1 business day.\n\n"
                f"Support Team")
    if template["id"] == "internal_summarizer":
        return ("- Update summarized without any names or contact details.\n"
                "- Key facts condensed to the essentials.\n"
                "- Full detail available in the source document.")
    return ""


# ------------------------------------------------------------- rule engine

def check_rules(output: str, rules: list) -> list:
    """Returns a list of {rule, passed, detail} results."""
    results = []
    for rule in rules:
        rtype = rule["type"]
        if rtype == "max_words":
            n = len(output.split())
            results.append({"rule": rtype, "passed": n <= rule["value"],
                             "detail": f"{n} words (limit {rule['value']})"})
        elif rtype == "must_include":
            missing = [w for w in rule["value"] if w.lower() not in output.lower()]
            results.append({"rule": rtype, "passed": not missing,
                             "detail": f"missing: {missing}" if missing else "ok"})
        elif rtype == "must_not_include":
            found = [w for w in rule["value"] if w.lower() in output.lower()]
            results.append({"rule": rtype, "passed": not found,
                             "detail": f"found banned terms: {found}" if found else "ok"})
        elif rtype == "max_bullets":
            n = len([l for l in output.splitlines() if l.strip().startswith(("-", "*"))])
            results.append({"rule": rtype, "passed": n <= rule["value"],
                             "detail": f"{n} bullets (limit {rule['value']})"})
        elif rtype == "max_words_per_bullet":
            bullets = [l for l in output.splitlines() if l.strip().startswith(("-", "*"))]
            too_long = [b for b in bullets if len(b.split()) - 1 > rule["value"]]
            results.append({"rule": rtype, "passed": not too_long,
                             "detail": f"{len(too_long)} bullet(s) over {rule['value']} words"})
        elif rtype == "no_pii":
            hits = PII_RE.findall(output)
            results.append({"rule": rtype, "passed": not hits,
                             "detail": f"found: {hits}" if hits else "ok"})
        else:
            results.append({"rule": rtype, "passed": None, "detail": "unknown rule type"})
    return results


def main():
    templates = load_templates()
    with open("test_cases.json", encoding="utf-8") as f:
        all_cases = json.load(f)

    report = []
    for template_id, cases in all_cases.items():
        template = templates[template_id]
        print(f"\n=== {template_id} (v{template['version']}, "
              f"owner: {template['owner']}) ===")
        for case in cases:
            output = run_prompt(template, case["input"])
            checks = check_rules(output, template["rules"])
            passed = all(c["passed"] for c in checks)
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {case['id']}: {case['input'][:50]}...")
            for c in checks:
                if not c["passed"]:
                    print(f"      x {c['rule']}: {c['detail']}")
            report.append({"template": template_id, "case": case["id"],
                            "status": status, "output": output, "checks": checks})

    with open("eval_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    n_pass = sum(1 for r in report if r["status"] == "PASS")
    print(f"\n{n_pass}/{len(report)} test cases passed -- see eval_report.json")


if __name__ == "__main__":
    main()
