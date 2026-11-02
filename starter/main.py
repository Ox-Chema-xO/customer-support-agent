"""
Customer support AI agent on Amazon Bedrock AgentCore.

Entry point of the Strands agent deployed to AgentCore Runtime. Setup, local
runs and deployment are described in the repository README.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import uuid
from typing import Dict

import boto3
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.code_interpreter_client import code_session
from mcp.client.streamable_http import streamable_http_client
from strands import Agent, tool
from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")
# Libraries stay at WARNING; our own messages (tool usage, memory activity)
# are INFO so they show up locally and in the runtime's CloudWatch logs.
logger.setLevel(logging.INFO)

# ── App and configuration ─────────────────────────────────────────────────────

# One BedrockAgentCoreApp per deployment: it serves /invocations and /ping.
app = BedrockAgentCoreApp()

# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# IDs of the resources created during the infrastructure setup (see README).
# The Gateway uses the NONE authorizer, so the MCP connection is unsigned.
GATEWAY_URL = "<gateway_url>"   # https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
KB_ID       = "<kb_id>"         # 10-character Knowledge Base ID
REGION      = "us-east-1"
MEMORY_ID   = "<memory_id>"     # e.g. CustomerSupportMemory-XXXXXXXXXX

# ── Model and AWS clients ─────────────────────────────────────────────────────
# Created once per container and reused by every request.

model_id = "global.amazon.nova-2-lite-v1:0"

# Pin the model client to REGION rather than the environment's default region.
model = BedrockModel(model_id=model_id, region_name=REGION)

memory_client = MemoryClient(region_name=REGION)

_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)

# ── Long-term memory (AgentCore Memory) ───────────────────────────────────────
# Each customer is a memory actor. The memory's strategies (SEMANTIC facts and
# USER_PREFERENCE) extract long-term records from the saved turns in the
# background, into namespaces that include {actorId}, so records are kept
# per customer.

# Reads namespaceTemplates and falls back to the older namespaces field.
def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    namespaces = {}
    for strategy in mem_client.get_memory_strategies(memory_id):
        templates = strategy.get("namespaceTemplates") or strategy.get("namespaces") or []
        if templates:
            namespaces[strategy["type"]] = templates[0]
    return namespaces


def is_customer_text(message: dict) -> bool:
    """True for a plain-text user message, False for tool results.

    Strands adds tool results to the conversation with role "user" too, so a
    role check alone would also match them.
    """
    content = message.get("content") or []
    return (
        message.get("role") == "user"
        and bool(content)
        and "text" in content[0]
        and not any("toolResult" in block for block in content)
    )


class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_id = memory_id
        self.memory_client = memory_client
        # Maps a context-augmented message back to what the customer typed.
        self.original_text: Dict[str, str] = {}

        # If memory is unavailable the agent still answers, just without
        # long-term context: with no namespaces the hook retrieves nothing.
        try:
            self.namespaces = get_namespaces(memory_client, memory_id)
        except Exception:
            logger.exception("Memory | could not load namespaces")
            self.namespaces = {}

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        messages = event.agent.messages
        if not messages or not is_customer_text(messages[-1]):
            return

        content = messages[-1]["content"]
        user_query = content[0]["text"]

        try:
            memories = []
            for strategy_type, namespace in self.namespaces.items():
                records = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=namespace.format(actorId=self.actor_id),
                    query=user_query,
                    top_k=5,
                )
                for record in records:
                    text = record.get("content", {}).get("text", "").strip()
                    if text:
                        memories.append(f"[{strategy_type}] {text}")
        except Exception:
            logger.exception("Memory | retrieval failed | actor=%s", self.actor_id)
            return

        logger.info("Memory | retrieved %d records | actor=%s", len(memories), self.actor_id)
        if memories:
            augmented = "Customer Context:\n" + "\n".join(memories) + "\n\n" + user_query
            content[0]["text"] = augmented
            self.original_text[augmented] = user_query

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        customer_query = agent_response = None
        for message in reversed(event.agent.messages):
            if message.get("role") == "assistant" and agent_response is None:
                agent_response = next(
                    (block["text"] for block in message.get("content", []) if "text" in block),
                    None,
                )
            elif is_customer_text(message):
                customer_query = message["content"][0]["text"]
                break

        if not customer_query or not agent_response:
            return
        # Save what the customer actually wrote, not the Customer Context block
        # prepended above — otherwise old memories are re-ingested every turn.
        customer_query = self.original_text.get(customer_query, customer_query)

        try:
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")],
            )
            logger.info("Memory | saved turn | actor=%s session=%s", self.actor_id, self.session_id)
        except Exception:
            logger.exception("Memory | save failed | actor=%s", self.actor_id)

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)

# ── Knowledge base tool (RAG) ─────────────────────────────────────────────────

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID:
        return "Knowledge base not configured: KB_ID is empty, so product and policy information is unavailable."

    try:
        # Managed KB: knowledgeBaseId + retrievalQuery is the documented minimal
        # request (hybrid search + managed reranking, up to 5 chunks).
        # vectorSearchConfiguration is not used; it only applies to non-managed KBs.
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
    except Exception:
        # Log it here (the model only sees a tool error), then let Strands
        # report the failure to the model as an error tool result.
        logger.exception("KB search failed | query=%r", query)
        raise

    chunks = [
        r["content"]["text"]
        for r in resp.get("retrievalResults", [])
        if r.get("content", {}).get("text")
    ]
    logger.info("KB search | query=%r chunks=%d", query, len(chunks))
    if not chunks:
        return f"No information found in the knowledge base for: {query}"

    return "\n---\n".join(chunks)

# ── Loyalty discount tool (Code Interpreter) ──────────────────────────────────

# Business rules, shared by the sandbox program and the fallback.
EARN_RATES = {"standard": 1, "device": 2, "fresh": 5}           # points per $1 paid
TIER_RATES = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}   # tier discount

# Program executed in the Code Interpreter sandbox. The inputs and both rate
# tables are prepended as literals, so the code that runs is self-contained.
# Decimal avoids float artefacts in money (0.1 + 0.2 == 0.30000000000000004).
DISCOUNT_PROGRAM = '''
import json
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
POINTS_PER_DOLLAR = 100                # 100 points = $1 discount
REDEMPTION_STEP = 500                  # redeem in blocks of 500 (minimum 500)
MAX_REDEMPTION_SHARE = Decimal("0.5")  # points may cover at most 50% of the order

total = Decimal(str(order_total))

# 1. Points: floor to a multiple of 500, capped at 50% of the order total.
max_points = int(total * MAX_REDEMPTION_SHARE * POINTS_PER_DOLLAR)
points_redeemed = min(loyalty_points, max_points) // REDEMPTION_STEP * REDEMPTION_STEP
points_discount = Decimal(points_redeemed) / POINTS_PER_DOLLAR

# 2. Tier discount, applied to the subtotal after points.
subtotal = total - points_discount
tier_rate = Decimal(str(tier_rates[tier]))
tier_discount = (subtotal * tier_rate).quantize(CENT, rounding=ROUND_HALF_UP)
final_total = (subtotal - tier_discount).quantize(CENT, rounding=ROUND_HALF_UP)

# 3. Points are earned on the amount actually paid; the new balance includes them.
points_earned = int(final_total * earn_rates[product_category])
remaining_points = loyalty_points - points_redeemed + points_earned

print(json.dumps({
    "tier": tier,
    "product_category": product_category,
    "order_total": float(total),
    "points_redeemed": points_redeemed,
    "points_discount": float(points_discount),
    "subtotal_after_points": float(subtotal),
    "tier_discount_pct": float(tier_rate * 100),
    "tier_discount": float(tier_discount),
    "final_total": float(final_total),
    "total_savings": float(points_discount + tier_discount),
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}))
'''

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.
    Use this whenever the customer asks how much they would pay or save on
    an order with their loyalty points and tier.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    tier_key = tier.strip().capitalize()
    category = product_category.strip().lower()
    if tier_key not in TIER_RATES:
        return json.dumps({"error": f"Unknown tier {tier!r}; expected one of {list(TIER_RATES)}."})
    if category not in EARN_RATES:
        return json.dumps({"error": f"Unknown product category {product_category!r}; expected one of {list(EARN_RATES)}."})
    if order_total <= 0 or loyalty_points < 0:
        return json.dumps({"error": "order_total must be positive and loyalty_points cannot be negative."})

    # repr() turns each value into a valid Python literal, so unusual
    # arguments can't break the program or inject code into it.
    code = (
        f"loyalty_points = {int(loyalty_points)!r}\n"
        f"tier = {tier_key!r}\n"
        f"order_total = {float(order_total)!r}\n"
        f"product_category = {category!r}\n"
        f"earn_rates = {EARN_RATES!r}\n"
        f"tier_rates = {TIER_RATES!r}\n"
        + DISCOUNT_PROGRAM
    )

    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke("executeCode", {
                "code": code,
                "language": "python",
                "clearContext": True,   # fresh sandbox state for every call
            })
            # Read the stream while the session is still open.
            result = next(event["result"] for event in response["stream"])

        if result.get("isError"):
            raise RuntimeError(result.get("structuredContent", {}).get("stderr") or str(result))

        # Return the program's JSON itself, so the breakdown fields are
        # top-level keys rather than text buried inside the raw event.
        breakdown = json.loads(result["structuredContent"]["stdout"])
        breakdown["calculated_by"] = "code_interpreter"
        logger.info("Code Interpreter | discount calculated | final_total=%s", breakdown["final_total"])
        return json.dumps(breakdown)

    except Exception:
        # The fallback's numbers look plausible even though the sandbox did not
        # run, so it is clearly marked both in the logs and in the result.
        logger.exception("Code Interpreter unavailable, using tier-only fallback")
        rate = TIER_RATES[tier_key]
        tier_discount = round(order_total * rate, 2)
        return json.dumps({
            "tier": tier_key,
            "order_total": order_total,
            "points_redeemed": 0,
            "tier_discount_pct": rate * 100,
            "tier_discount": tier_discount,
            "final_total": round(order_total - tier_discount, 2),
            "remaining_points": loyalty_points,
            "calculated_by": "fallback_tier_only",
            "note": "Code Interpreter unavailable: tier discount only, no points redeemed.",
        })


# ── Tool audit hook ───────────────────────────────────────────────────────────
# The model tends to summarise tool errors in friendly sentences and
# `agentcore invoke` only shows the final answer, so logging each call's
# arguments, status and result shows what every tool actually received and
# returned, locally and in CloudWatch.

class ToolAuditHook(HookProvider):
    """Log every tool call the agent makes."""

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        registry.add_callback(AfterToolCallEvent, self.log_tool_call)

    def log_tool_call(self, event: AfterToolCallEvent) -> None:
        result = event.result or {}
        text = " ".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict)
        )
        logger.info(
            "Tool call | %s input=%s status=%s result=%.300s",
            event.tool_use["name"],
            json.dumps(event.tool_use.get("input", {})),
            result.get("status"),
            text,
        )


# ── Tool input fix hook ───────────────────────────────────────────────────────
# Two arguments the model often gets wrong:
#   - basePath: the REST API's OpenAPI export declares its server as
#     ".../{basePath}" (default "prod"), so the Gateway exposes it as an
#     optional argument of every API Gateway tool. When the model fills it
#     in, "/" is rejected and "" drops the stage (API Gateway answers 403).
#     Removing it makes the Gateway always use the default stage.
#   - browser session_name: init_session only accepts ^[a-z0-9-]+$ with 10-36
#     characters, and the model often uses underscores ("udacity_session").
#     Every browser action gets the same normalised name, so later actions
#     still find the session.

def normalise_session_name(name: str) -> str:
    """Map any session name to a valid one (^[a-z0-9-]+$, 10-36 chars), deterministically."""
    name = re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "session"
    if len(name) < 10:
        name = f"{name}-session"
    return name.ljust(10, "0")[:36]


class ToolInputFixHook(HookProvider):
    """Correct known-bad tool arguments before each call."""

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        registry.add_callback(BeforeToolCallEvent, self.fix_input)

    def fix_input(self, event: BeforeToolCallEvent) -> None:
        tool_input = event.tool_use.get("input")
        if not isinstance(tool_input, dict):
            return

        if "basePath" in tool_input:
            removed = tool_input.pop("basePath")
            logger.info("Removed basePath=%r from %s", removed, event.tool_use["name"])

        action = (tool_input.get("browser_input") or {}).get("action")
        if event.tool_use["name"] == "browser" and isinstance(action, dict) and "session_name" in action:
            fixed = normalise_session_name(action["session_name"])
            if fixed != action["session_name"]:
                logger.info("Browser session_name %r -> %r", action["session_name"], fixed)
                action["session_name"] = fixed


# ── Ownership guard hook ──────────────────────────────────────────────────────
# The system prompt tells the agent to stay within the signed-in customer's
# data, but the model does not always follow prompt rules. This hook enforces
# it in code:
#   - get_customer / get_customer_orders only run for the signed-in customer;
#   - a get_order result that belongs to another customer is replaced with
#     "not found" before the model sees it, so it can neither leak it nor
#     reveal that the order exists;
#   - initiate_refund / get_return_label only run for orders already verified
#     as the customer's own during this invocation.
# Defence in depth only: a production backend must authorise every request
# against the authenticated identity itself.

class OwnershipGuardHook(HookProvider):
    """Keep Gateway tool calls within the signed-in customer's own data."""

    CUSTOMER_TOOLS = ("get_customer", "get_customer_orders")
    ORDER_ACTION_TOOLS = ("initiate_refund", "get_return_label")

    def __init__(self, customer_id: str):
        self.customer_id = customer_id.upper()
        self.verified_orders: set = set()

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        registry.add_callback(BeforeToolCallEvent, self.check_request)
        registry.add_callback(AfterToolCallEvent, self.check_result)

    @staticmethod
    def short_name(tool_use: dict) -> str:
        """Tool name without the Gateway target prefix (target___tool)."""
        return tool_use["name"].split("___")[-1]

    def check_request(self, event: BeforeToolCallEvent) -> None:
        name = self.short_name(event.tool_use)
        args = event.tool_use.get("input") or {}

        if name in self.CUSTOMER_TOOLS:
            if str(args.get("customer_id", "")).upper() != self.customer_id:
                event.cancel_tool = "Access denied: only the signed-in customer's own account can be looked up."
        elif name in self.ORDER_ACTION_TOOLS:
            order_id = str(args.get("order_id", "")).upper()
            if order_id not in self.verified_orders:
                event.cancel_tool = (
                    f"Order {order_id} is not verified for this customer. Look it up with "
                    "get_order first; only the customer's own orders can be refunded or labelled."
                )

        if event.cancel_tool:
            logger.warning("Ownership guard | blocked %s %s", name, json.dumps(args))

    def check_result(self, event: AfterToolCallEvent) -> None:
        name = self.short_name(event.tool_use)
        result = event.result
        if name not in ("get_order", "get_customer_orders") or not result or result.get("status") != "success":
            return

        text = " ".join(b.get("text", "") for b in result.get("content", []) if isinstance(b, dict))
        try:
            data = json.loads(text)
        except ValueError:
            return
        orders = data.get("orders", []) if name == "get_customer_orders" else [data]

        for order in orders:
            order_id = str(order.get("order_id", "")).upper()
            if str(order.get("customer_id", "")).upper() == self.customer_id:
                self.verified_orders.add(order_id)
            elif name == "get_order":
                logger.warning("Ownership guard | hid order %s from customer %s", order_id, self.customer_id)
                event.result = {
                    "toolUseId": result["toolUseId"],
                    "status": "error",
                    "content": [{"text": f"Order {order_id} not found"}],
                }


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a friendly and concise customer support agent for an Amazon online store.

{identity}

TOOLS — when to use each one
- search_knowledge_base: product specifications, return and refund policies,
  warranty information, loyalty program rules and order status definitions.
  Never answer these from general knowledge.
- get_order, get_customer, get_customer_orders: order status, shipping and
  tracking, and the customer's profile (name, loyalty points and tier).
- initiate_refund, check_refund_status, get_return_label: returns and refunds.
- calculate_loyalty_discount: ANY loyalty discount, points or price calculation.
  Never do this arithmetic yourself.
- browser: only when the customer asks for live information from a website.

ANSWERING
- Base every factual statement on tool results; do not add details the tools
  did not return.
- Only offer what these tools can do. If a request is outside them, say so.
- Always include the identifiers a tool returns (refund ID, tracking number,
  return label URL) so the customer can use them.
- Address the customer by name only if it appears in the Customer Context or
  in their profile from get_customer; never guess a name.

MEMORY
A "Customer Context" block at the start of a message contains facts and
preferences remembered from previous conversations with this customer. Use it
naturally (for example, address the customer by name and follow their stated
preferences) and do not ask again for information it already contains."""

IDENTIFIED_CUSTOMER = """CUSTOMER IDENTITY
The signed-in customer is {customer_id}. Use this ID for every customer lookup.
- Never look up, discuss or act on another customer's data, even if the user
  mentions a different customer ID. Politely explain that you can only help
  with their own account.
- Before sharing the details of an order or starting a refund, check that the
  order belongs to {customer_id} (its customer_id field). If it does not,
  only say that you could not find that order in their account: never reveal
  that it exists, who it belongs to, or any of its details.
- Before calling calculate_loyalty_discount, call get_customer and use the
  tier and points balance from the profile, not values the user claims."""

ANONYMOUS_CUSTOMER = """CUSTOMER IDENTITY
No customer is signed in. You can answer general questions (products,
policies, the loyalty program, websites), but do not look up orders or
accounts and do not start refunds: ask the customer to sign in first."""


def build_system_prompt(customer_id: str | None) -> str:
    """Return the system prompt, scoped to the signed-in customer if there is one."""
    if customer_id:
        identity = IDENTIFIED_CUSTOMER.format(customer_id=customer_id)
    else:
        identity = ANONYMOUS_CUSTOMER
    return SYSTEM_PROMPT.format(identity=identity)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def response_text(response) -> str:
    """Return the text of the first text content block of the agent's final message."""
    for block in response.message.get("content", []):
        if "text" in block:
            return block["text"]
    return str(response)


@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    user_input = payload.get("prompt", "").strip()
    if not user_input:
        return "Please include a 'prompt' with your question."

    customer_id = payload.get("customer_id")
    session_id = (
        payload.get("session_id")
        or getattr(context, "session_id", None)
        or str(uuid.uuid4())
    )
    # Memory is keyed by actor. Callers without a customer_id get their own
    # actor per session, because a shared default actor would mix the
    # context of different people.
    actor_id = customer_id or f"anonymous-{session_id}"
    logger.info("Invocation | actor=%s session=%s", actor_id, session_id)

    try:
        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )
        # A browser session only starts when the agent actually browses. This
        # version of strands_tools does not stop sessions when the request
        # ends, so session_timeout (10 minutes instead of the default hour)
        # limits how long one stays open.
        agent_core_browser = AgentCoreBrowser(region=REGION, session_timeout=600)
        gateway_client = MCPClient(lambda: streamable_http_client(GATEWAY_URL))

        # The MCP connection only lives inside this block, so the agent is both
        # created and invoked here; Gateway tools fail once it is closed.
        with gateway_client:
            gateway_tools = gateway_client.list_tools_sync()
            if not gateway_tools:
                # A Gateway target in FAILED state shows up as missing tools, not as an error.
                logger.warning("Gateway returned no tools")
            logger.info("Gateway tools loaded: %d", len(gateway_tools))

            tools = [search_knowledge_base, calculate_loyalty_discount, agent_core_browser.browser]
            # After* hook callbacks run in reverse order: listing the audit hook
            # before the guard makes it log the result the model actually sees.
            hooks = [memory_hook, ToolInputFixHook(), ToolAuditHook()]

            if customer_id:
                tools.extend(gateway_tools)
                hooks.append(OwnershipGuardHook(customer_id))
            else:
                # Every Gateway tool reads or changes customer data, so anonymous
                # callers don't get them at all; a prompt rule alone proved unreliable.
                logger.info("Anonymous caller: Gateway tools not loaded")

            agent = Agent(
                model=model,
                system_prompt=build_system_prompt(customer_id),
                tools=tools,
                hooks=hooks,
            )
            response = agent(user_input)

        return response_text(response)

    except Exception:
        # Full details go to the logs; the customer gets a safe, generic reply.
        logger.exception("Invocation failed | actor=%s session=%s", actor_id, session_id)
        return "Sorry, something went wrong while handling your request. Please try again."


# ── Local CLI ─────────────────────────────────────────────────────────────────

def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # For a one-off run without the HTTP server, call main() instead.
