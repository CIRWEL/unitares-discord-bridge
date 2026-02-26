"""Live HUD embed builder and auto-updating loop for governance dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import discord

from bridge.cache import BridgeCache
from bridge.mcp_client import AnimaClient, GovernanceClient
from bridge.tasks import create_logged_task

log = logging.getLogger(__name__)

VERDICT_EMOJI = {
    "proceed": "\U0001f7e2",   # green circle
    "guide": "\U0001f7e1",     # yellow circle
    "pause": "\U0001f534",     # red circle
    "reject": "\u26d4",        # no entry
}

DEFAULT_METRICS = {"E": 0.0, "I": 0.0, "S": 0.0, "V": 0.0, "verdict": "guide"}


def build_hud_embed(
    agents: list[dict],
    metrics: dict[str, dict],
    connection_status: dict[str, bool] | None = None,
) -> discord.Embed:
    """Build a Discord embed summarising all active agents and their EISV metrics.

    Parameters
    ----------
    agents:
        List of dicts with ``"id"`` and ``"label"`` keys.
    metrics:
        Dict keyed by agent id, values are dicts with ``"E"``, ``"I"``,
        ``"S"``, ``"V"``, and ``"verdict"`` keys.
    """
    embed = discord.Embed(
        title="UNITARES Governance \u2014 Live",
        colour=discord.Colour.blurple(),
    )

    conn_line = ""
    if connection_status:
        parts = []
        for svc, ok in connection_status.items():
            parts.append(f"{svc}: {'OK' if ok else 'DOWN'}")
        conn_line = " | ".join(parts) + "\n"

    if not agents:
        embed.description = "No active agents"
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        embed.set_footer(text=f"{conn_line}0 agents | 0 paused | 0 boundary | Updated {now}")
        return embed

    lines: list[str] = []
    paused = 0
    boundary = 0

    for agent in agents:
        agent_id = agent["id"]
        label = agent["label"]
        m = metrics.get(agent_id, DEFAULT_METRICS)
        verdict = m.get("verdict", "guide")
        emoji = VERDICT_EMOJI.get(verdict, "\u2753")

        if verdict == "pause":
            paused += 1
        if verdict == "guide":
            boundary += 1

        e_val = m.get("E", 0.0)
        i_val = m.get("I", 0.0)
        s_val = m.get("S", 0.0)
        v_val = m.get("V", 0.0)

        lines.append(
            f"{emoji} **{label}**  "
            f"E={e_val:.2f}  I={i_val:.2f}  S={s_val:.2f}  V={v_val:.2f}"
        )

    embed.description = "\n".join(lines)
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    embed.set_footer(
        text=f"{conn_line}{len(agents)} agents | {paused} paused | {boundary} boundary | Updated {now}"
    )
    return embed


class HUDUpdater:
    """Periodically updates a Discord embed with live governance metrics."""

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        hud_channel: discord.TextChannel,
        interval: int = 30,
        anima_client: AnimaClient | None = None,
    ) -> None:
        self.gov = gov_client
        self.anima = anima_client
        self.cache = cache
        self.hud_channel = hud_channel
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._message: discord.Message | None = None

    async def start(self) -> None:
        """Restore the HUD message from cache or create a new one, then start the loop."""
        cached = await self.cache.get_hud_message()
        if cached is not None:
            channel_id, message_id = cached
            try:
                self._message = await self.hud_channel.fetch_message(message_id)
                log.info("Restored HUD message %d from cache", message_id)
            except discord.NotFound:
                log.info("Cached HUD message %d not found, will create new", message_id)
                self._message = None

        if self._message is None:
            embed = build_hud_embed([], {})
            self._message = await self.hud_channel.send(embed=embed)
            await self.cache.set_hud_message(self.hud_channel.id, self._message.id)
            log.info("Created new HUD message %d", self._message.id)

        self._task = create_logged_task(self._update_loop(), name="hud-update")

    async def stop(self) -> None:
        """Cancel the update loop task."""
        if self._task:
            self._task.cancel()

    async def _update_loop(self) -> None:
        while True:
            try:
                agents = await self._fetch_agents()
                metrics = await self._fetch_metrics(agents)
                conn = {
                    "Governance": self.gov.consecutive_failures == 0,
                }
                if self.anima is not None:
                    conn["Lumen"] = self.anima.is_online
                embed = build_hud_embed(agents, metrics, connection_status=conn)
                if self._message is not None:
                    await self._message.edit(embed=embed)
            except Exception as exc:
                log.error("HUD update error: %s", exc)
            await asyncio.sleep(self.interval)

    async def _fetch_agents(self) -> list[dict]:
        """Call list_agents via governance MCP and return list of agent dicts."""
        result = await self.gov.call_tool("list_agents", {})
        if result is None:
            return []
        try:
            # MCP wraps: result["result"]["content"][0]["text"] is a JSON string
            inner = result.get("result", result)
            content = inner.get("content", [])
            if content:
                text = content[0].get("text", "[]")
                data = json.loads(text)
            else:
                data = inner if isinstance(inner, list) else []
            # Normalise to list of {"id": ..., "label": ...}
            agents = []
            for item in data if isinstance(data, list) else [data]:
                agent_id = item.get("agent_id") or item.get("id", "")
                label = item.get("label") or item.get("name") or agent_id
                agents.append({"id": agent_id, "label": label})
            return agents
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            log.warning("Failed to parse list_agents response: %s", exc)
            return []

    async def _fetch_metrics(self, agents: list[dict]) -> dict[str, dict]:
        """Call get_governance_metrics for each agent and return metrics dict."""
        metrics: dict[str, dict] = {}
        for agent in agents:
            agent_id = agent["id"]
            result = await self.gov.call_tool(
                "get_governance_metrics", {"agent_id": agent_id}
            )
            if result is None:
                continue
            try:
                inner = result.get("result", result)
                content = inner.get("content", [])
                if content:
                    text = content[0].get("text", "{}")
                    data = json.loads(text)
                else:
                    data = inner if isinstance(inner, dict) else {}
                metrics[agent_id] = {
                    "E": data.get("E", data.get("entropy", 0.0)),
                    "I": data.get("I", data.get("integration", 0.0)),
                    "S": data.get("S", data.get("stability", 0.0)),
                    "V": data.get("V", data.get("volatility", 0.0)),
                    "verdict": data.get("verdict", "guide"),
                }
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                log.warning("Failed to parse metrics for %s: %s", agent_id, exc)
        return metrics
