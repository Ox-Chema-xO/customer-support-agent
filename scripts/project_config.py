"""Shared helpers for the scripts: read the agent's settings without importing or running it."""
import ast
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
STARTER = REPO / "starter"


def main_settings() -> dict:
    """String literals assigned at module level in starter/main.py (GATEWAY_URL, KB_ID, ...)."""
    settings = {}
    for node in ast.parse((STARTER / "main.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"GATEWAY_URL", "KB_ID", "REGION", "MEMORY_ID"}:
                    settings[target.id] = ast.literal_eval(node.value)
    return settings


def runtime_agent() -> dict:
    """Name, ID and ARN of the default agent in starter/.bedrock_agentcore.yaml."""
    config = yaml.safe_load((STARTER / ".bedrock_agentcore.yaml").read_text(encoding="utf-8"))
    name = config["default_agent"]
    deployed = config["agents"][name]["bedrock_agentcore"]
    return {"name": name, "agent_id": deployed["agent_id"], "agent_arn": deployed["agent_arn"]}
