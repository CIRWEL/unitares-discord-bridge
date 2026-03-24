"""Shared utilities for the discord bridge.

Centralises the MCP tool-result envelope unwrapping and the common
agent/metrics fetch helpers so that hud.py, commands.py, and the
deferred extensions all use a single, consistent implementation.
"""

from __future__ import annotations

import json
import logging

from bridge.mcp_client import GovernanceClient

log = logging.getLogger(__name__)


def parse_tool_result(result: dict | None) -> dict | list:
    """Unwrap the MCP tool result envelope.

    MCP tool responses carry the payload inside:
        result["result"]["content"][0]["text"]  (a JSON string)

    Returns {} when *result* is None or the envelope is empty so callers
    never have to guard against None themselves.
    """
    # None guard — callers that receive None from call_tool() are safe
    if result is None:
        return {}
    content = result.get("result", {}).get("content", [])
    if content:
        text = content[0].get("text", "{}")
        return json.loads(text)
    return {}


async def fetch_agents(gov_client: GovernanceClient) -> list[dict]:
    """Call list_agents via *gov_client* and return normalised agent dicts.

    Each dict has ``"id"`` and ``"label"`` keys.  Returns [] on failure.
    """
    result = await gov_client.call_tool("list_agents", {})
    if result is None:
        return []
    try:
        data = parse_tool_result(result)
        agents: list[dict] = []
        items = data if isinstance(data, list) else [data]
        for item in items:
            agent_id = item.get("agent_id") or item.get("id", "")
            label = item.get("label") or item.get("name") or agent_id
            agents.append({"id": agent_id, "label": label})
        return agents
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        log.warning("Failed to parse list_agents: %s", exc)
        return []


async def fetch_metrics(
    gov_client: GovernanceClient, agents: list[dict],
) -> dict[str, dict]:
    """Fetch EISV metrics for each agent via *gov_client*.

    Returns a dict keyed by agent_id with E, I, S, V, and verdict.
    Uses correct fallback key names: energy, integration, entropy, volatility.
    """
    metrics: dict[str, dict] = {}
    for agent in agents:
        agent_id = agent["id"]
        result = await gov_client.call_tool(
            "get_governance_metrics", {"agent_id": agent_id},
        )
        if result is None:
            continue
        try:
            data = parse_tool_result(result)
            if isinstance(data, list):
                data = data[0] if data else {}
            metrics[agent_id] = {
                # Fallback keys match the UNITARES model names:
                # E=Energy, I=Information Integrity, S=Entropy, V=Void
                "E": data.get("E", data.get("energy", 0.0)),
                "I": data.get("I", data.get("integration", 0.0)),
                "S": data.get("S", data.get("entropy", 0.0)),
                "V": data.get("V", data.get("volatility", 0.0)),
                "verdict": data.get("verdict", "guide"),
            }
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            log.warning("Failed to parse metrics for %s: %s", agent_id, exc)
    return metrics
