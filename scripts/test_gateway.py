"""Talk to the AgentCore Gateway directly over MCP — no model, no memory, no agent.

Answers "is the Gateway broken, or is something above it broken?" in one run:
  1. Lists every tool the Gateway advertises (name, description, arguments) and
     flags names that Bedrock Converse would reject.
  2. Calls each tool with known-good and known-bad inputs and prints the raw result.

Works because the Gateway uses the NONE authorizer (unsigned MCP connection).

Usage (from the repository root):
  uv run --project starter python scripts/test_gateway.py [GATEWAY_URL]
GATEWAY_URL defaults to $GATEWAY_URL, then to the literal in starter/main.py.
"""
import ast
import os
import re
import sys
import uuid
from pathlib import Path

from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp.mcp_client import MCPClient

MAIN_PY = Path(__file__).resolve().parents[1] / "starter" / "main.py"

# Bedrock Converse ToolSpecification.name constraint (botocore service model).
BEDROCK_TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")

# (tool name without target prefix, arguments, what we expect)
CASES = [
    ("get_order", {"order_id": "ORD-001"}, "OK"),
    ("get_order", {"order_id": "ORD-999"}, "404 -> error"),
    ("get_customer", {"customer_id": "CUST-123"}, "OK"),
    ("get_customer", {"customer_id": "CUST-999"}, "404 -> error"),
    ("get_customer_orders", {"customer_id": "CUST-456"}, "OK"),
    ("get_customer_orders", {"customer_id": "CUST-999"}, "404 -> error"),
    ("initiate_refund", {"order_id": "ORD-002", "reason": "Gateway diagnostic test", "amount": 139.99}, "OK"),
    ("check_refund_status", {"refund_id": "REF-TEST0001"}, "OK"),
    ("get_return_label", {"order_id": "ORD-002"}, "OK"),
]


def gateway_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("GATEWAY_URL"):
        return os.environ["GATEWAY_URL"]
    for node in ast.parse(MAIN_PY.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "GATEWAY_URL" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    sys.exit("GATEWAY_URL not found: pass it as an argument or set it in starter/main.py")


def list_all_tools(client: MCPClient) -> list:
    tools, token = [], None
    while True:
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        token = page.pagination_token
        if not token:
            return tools


def result_text(result: dict) -> str:
    return " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))


def main() -> int:
    url = gateway_url()
    print(f"Gateway: {url}\n")
    client = MCPClient(lambda: streamable_http_client(url))

    with client:
        tools = list_all_tools(client)
        print(f"== {len(tools)} tools advertised")
        bad_names = 0
        for tool in tools:
            spec = tool.tool_spec
            schema = spec.get("inputSchema", {}).get("json", {})
            ok = bool(BEDROCK_TOOL_NAME.fullmatch(tool.tool_name))
            bad_names += not ok
            print(f"{'  ' if ok else '!!'} {tool.tool_name!r}")
            print(f"     description: {spec.get('description')!r}")
            print(f"     args: {list(schema.get('properties', {}))}  required: {schema.get('required', [])}")
        if bad_names:
            print(f"\n!! {bad_names} tool name(s) do not match {BEDROCK_TOOL_NAME.pattern}: "
                  "Bedrock Converse will reject every agent request that includes them.")

        by_suffix = {tool.tool_name.strip().split("___")[-1]: tool.tool_name for tool in tools}
        print("\n== calls")
        for short_name, args, expected in CASES:
            name = by_suffix.get(short_name)
            if name is None:
                print(f"-- {short_name}: NOT ADVERTISED by the Gateway")
                continue
            result = client.call_tool_sync(f"diag-{uuid.uuid4().hex[:8]}", name, args)
            text = result_text(result)
            print(f"-- {name} {args}  [expected: {expected}]")
            print(f"   status={result['status']}  {text[:300]}")

    return 1 if bad_names else 0


if __name__ == "__main__":
    sys.exit(main())
