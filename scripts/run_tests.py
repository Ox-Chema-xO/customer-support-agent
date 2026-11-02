"""Run the six test scenarios against the deployed runtime and capture evidence.

For every scenario this script:
  - calls the real `agentcore invoke` CLI with the scenario payload, always
    with a customer_id (so memory is keyed to that customer) and a fresh
    runtime session ID (so no earlier conversation carries over);
  - prints the full command and terminal output;
  - pulls that runtime session's agent log lines from CloudWatch (tool calls
    with their input, status and result; memory; Code Interpreter), which is
    the evidence of what each tool actually returned.
Test 4 uses two different sessions for the same customer and, between them,
waits until long-term memory extraction has produced new records.

Writes docs/test-transcript.md (a partial run writes
docs/test-transcript-tests-<numbers>.md, so it never overwrites the full one).

Usage (from the repository root):
  uv run --project starter python scripts/run_tests.py          # all six
  uv run --project starter python scripts/run_tests.py 5        # only test 5
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3

from project_config import REPO, STARTER, main_settings, runtime_agent
from purge_memory import actor_namespaces, list_records

CUSTOMER_ID = "CUST-123"
TRANSCRIPT = REPO / "docs" / "test-transcript.md"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
AGENT_LOG = re.compile(r"CSAI_Agent:(.*)")

# number: (title, expected result, payloads in order)
SCENARIOS = {
    1: ("Order Tracking",
        "shipping status, tracking number TRK987654321, carrier UPS, estimated delivery",
        [{"prompt": "Can you track order ORD-001?", "customer_id": CUSTOMER_ID, "session_id": "t1"}]),
    2: ("Refund Processing",
        "refund ID, APPROVED status, 3-5 business days message",
        [{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.",
          "customer_id": CUSTOMER_ID, "session_id": "t2"}]),
    3: ("Knowledge Base (RAG)",
        "free same-day shipping, 15% discount, priority support",
        [{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": CUSTOMER_ID, "session_id": "t3"}]),
    4: ("Long-Term Memory",
        'agent recalls "Jane" and "concise responses" in a new session',
        [{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": CUSTOMER_ID, "session_id": "s-A"},
         {"prompt": "Do you remember my name and communication preference?", "customer_id": CUSTOMER_ID, "session_id": "s-B"}]),
    5: ("Loyalty Discount Calculation",
        "points redeemed, tier discount 10%, final total, remaining points",
        [{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.",
          "customer_id": CUSTOMER_ID, "session_id": "t5"}]),
    6: ("Browser Tool",
        "page title retrieved from live Udacity.com",
        [{"prompt": "Go to https://www.udacity.com and tell me the page title.", "customer_id": CUSTOMER_ID, "session_id": "t6"}]),
}


def invoke(payload: dict) -> dict:
    """Run `agentcore invoke` with a fresh runtime session and return what happened."""
    runtime_session = str(uuid.uuid4())
    payload_json = json.dumps(payload)
    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        ["uv", "run", "agentcore", "invoke", "--session-id", runtime_session, payload_json],
        cwd=STARTER,
        env=dict(os.environ, AGENTCORE_SUPPRESS_RECOMMENDATION="1", COLUMNS="160"),
        capture_output=True,
        text=True,
    )
    output = ANSI.sub("", proc.stdout if proc.returncode == 0 else proc.stdout + proc.stderr).strip()
    return {
        "command": f"agentcore invoke --session-id {runtime_session} '{payload_json}'",
        "runtime_session": runtime_session,
        "started": started,
        "output": output,
        "returncode": proc.returncode,
    }


def agent_log_lines(logs, log_group: str, run: dict, timeout: int = 120) -> list:
    """This runtime session's agent log lines, waiting for CloudWatch ingestion."""
    prefix = run["started"].strftime("%Y/%m/%d/") + f"[runtime-logs-{run['runtime_session']}]"
    deadline = time.time() + timeout
    while True:
        lines, token = [], None
        try:
            while True:
                kwargs = {"logGroupName": log_group, "logStreamNamePrefix": prefix, "filterPattern": '"CSAI_Agent"'}
                if token:
                    kwargs["nextToken"] = token
                page = logs.filter_log_events(**kwargs)
                for event in page.get("events", []):
                    match = AGENT_LOG.search(event["message"])
                    if match:
                        lines.append(match.group(1).strip())
                token = page.get("nextToken")
                if not token:
                    break
        except logs.exceptions.ResourceNotFoundException:
            pass
        finished = any("Memory | saved turn" in l or "Invocation failed" in l for l in lines)
        if finished or time.time() > deadline:
            return lines
        time.sleep(5)


def wait_for_new_memories(client, memory_id: str, namespaces: list, known_ids: set, must_contain: str,
                          minimum: int = 60, timeout: int = 300):
    """Wait (at least `minimum` seconds) until extraction adds a record mentioning `must_contain`.

    Records extracted from earlier tests can appear meanwhile, so "any new
    record" is not enough to know session A has been processed.
    """
    start = time.time()
    while True:
        elapsed = int(time.time() - start)
        new = [r for ns in namespaces for r in list_records(client, memory_id, ns)
               if r["memoryRecordId"] not in known_ids]
        found = any(must_contain.lower() in r["content"].get("text", "").lower() for r in new)
        if (found and elapsed >= minimum) or elapsed >= timeout:
            return elapsed, new
        time.sleep(10)


def emit(lines: list, text: str = "") -> None:
    print(text, flush=True)
    lines.append(text)


def report_run(md: list, label: str, run: dict, log_lines: list) -> None:
    emit(md, f"**{label}**" if label else "")
    emit(md, "```bash")
    emit(md, run["command"])
    emit(md, "```")
    emit(md, "Terminal output:")
    emit(md, "```")
    emit(md, run["output"])
    emit(md, "```")
    emit(md, f"Agent log lines from CloudWatch (runtime session `{run['runtime_session']}`):")
    emit(md, "```")
    for line in log_lines:
        emit(md, line)
    emit(md, "```")
    emit(md)


def main() -> int:
    selected = [int(a) for a in sys.argv[1:]] or sorted(SCENARIOS)
    settings = main_settings()
    agent = runtime_agent()
    region, memory_id = settings["REGION"], settings["MEMORY_ID"]
    log_group = f"/aws/bedrock-agentcore/runtimes/{agent['agent_id']}-DEFAULT"
    logs = boto3.client("logs", region_name=region)
    memory = boto3.client("bedrock-agentcore", region_name=region)
    namespaces = actor_namespaces(region, memory_id, CUSTOMER_ID)

    md: list = []
    emit(md, "# Test transcript — Customer Support Agent on Amazon Bedrock AgentCore")
    emit(md)
    emit(md, f"- Runtime: `{agent['agent_arn']}`")
    emit(md, f"- Region: `{region}` · Memory: `{memory_id}` · Customer: `{CUSTOMER_ID}`")
    emit(md, f"- Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    existing = sum(len(list_records(memory, memory_id, ns)) for ns in namespaces)
    emit(md, f"- Long-term memory records for {CUSTOMER_ID} before the run: {existing}")
    emit(md, "- Every invocation uses a fresh runtime session (`--session-id`) and passes `customer_id`.")
    emit(md)

    for number in selected:
        title, expected, payloads = SCENARIOS[number]
        emit(md, f"## Test {number} — {title}")
        emit(md)
        emit(md, f"Expected (README): {expected}")
        emit(md)

        if number == 4:
            known = {r["memoryRecordId"] for ns in namespaces for r in list_records(memory, memory_id, ns)}
            run_a = invoke(payloads[0])
            report_run(md, "Session A (s-A) — introduce yourself", run_a, agent_log_lines(logs, log_group, run_a))
            elapsed, new = wait_for_new_memories(memory, memory_id, namespaces, known, must_contain="Jane")
            emit(md, f"Waited {elapsed}s for long-term memory extraction. New records for {CUSTOMER_ID}:")
            emit(md, "```")
            for record in new:
                emit(md, record["content"].get("text", ""))
            emit(md, "```")
            emit(md)
            run_b = invoke(payloads[1])
            report_run(md, "Session B (s-B) — new session, same customer", run_b, agent_log_lines(logs, log_group, run_b))
        else:
            run = invoke(payloads[0])
            report_run(md, "", run, agent_log_lines(logs, log_group, run))

    transcript = TRANSCRIPT
    if selected != sorted(SCENARIOS):
        transcript = TRANSCRIPT.with_name(f"test-transcript-tests-{'-'.join(map(str, selected))}.md")
    transcript.parent.mkdir(exist_ok=True)
    transcript.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Transcript written to {transcript.relative_to(REPO)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
