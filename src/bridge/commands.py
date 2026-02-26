"""Discord slash commands for governance interaction."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

from bridge.mcp_client import GovernanceClient, AnimaClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response builders (pure functions, testable without Discord)
# ---------------------------------------------------------------------------

def build_status_embed(agents: list[dict], metrics: dict[str, dict]) -> discord.Embed:
    """Build an EISV status overview embed for all agents."""
    from bridge.hud import build_hud_embed
    return build_hud_embed(agents, metrics)


def build_agent_embed(agent_id: str, data: dict) -> discord.Embed:
    """Build a detailed agent view embed."""
    label = data.get("label") or data.get("name") or agent_id
    verdict = data.get("verdict", "unknown")
    embed = discord.Embed(
        title=f"Agent: {label}",
        colour=_verdict_colour(verdict),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="ID", value=agent_id[:12], inline=True)
    embed.add_field(name="Verdict", value=verdict, inline=True)
    embed.add_field(
        name="EISV",
        value=(
            f"E={data.get('E', 0.0):.2f}  I={data.get('I', 0.0):.2f}  "
            f"S={data.get('S', 0.0):.2f}  V={data.get('V', 0.0):.2f}"
        ),
        inline=False,
    )
    if data.get("trajectory"):
        embed.add_field(name="Trajectory", value=str(data["trajectory"]), inline=False)
    if data.get("last_seen"):
        embed.add_field(name="Last Seen", value=str(data["last_seen"]), inline=True)
    return embed


def build_health_embed(health: dict) -> discord.Embed:
    """Build a system health embed from /health response."""
    status = health.get("status", "unknown")
    colour = discord.Colour.green() if status == "ok" else discord.Colour.red()
    embed = discord.Embed(
        title="System Health",
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Status", value=status, inline=True)

    # Uptime may be a dict {"seconds": N, "formatted": "Xm Ys"} or a string
    uptime = health.get("uptime", "?")
    if isinstance(uptime, dict):
        uptime = uptime.get("formatted", f"{uptime.get('seconds', '?')}s")
    embed.add_field(name="Uptime", value=str(uptime), inline=True)

    # Connection count from the connections block
    connections = health.get("connections", {})
    if isinstance(connections, dict):
        embed.add_field(
            name="Connections", value=str(connections.get("active", "?")), inline=True,
        )

    # Database status may be a dict {"status": "connected", ...} or a string
    db = health.get("database", health.get("db", "?"))
    if isinstance(db, dict):
        db = db.get("status", "?")
    embed.add_field(name="Database", value=str(db), inline=True)

    version = health.get("version", "")
    if version:
        embed.set_footer(text=f"v{version}")
    return embed


def build_resume_embed(agent_id: str, result: dict) -> discord.Embed:
    """Build a resume confirmation embed."""
    success = result.get("success", result.get("resumed", False))
    colour = discord.Colour.green() if success else discord.Colour.red()
    embed = discord.Embed(
        title="Agent Resume",
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    status_text = "Resumed" if success else "Failed"
    embed.add_field(name="Agent", value=agent_id[:12], inline=True)
    embed.add_field(name="Status", value=status_text, inline=True)
    message = result.get("message", result.get("reason", ""))
    if message:
        embed.description = message
    return embed


def build_lumen_embed(state: dict) -> discord.Embed:
    """Build a Lumen state embed from anima /state response."""
    from bridge.lumen import build_sensor_embed
    return build_sensor_embed(state)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verdict_colour(verdict: str) -> discord.Colour:
    return {
        "proceed": discord.Colour.green(),
        "guide": discord.Colour.gold(),
        "pause": discord.Colour.red(),
        "reject": discord.Colour.dark_red(),
    }.get(verdict, discord.Colour.greyple())


def _parse_tool_result(result: dict) -> dict | list:
    """Unwrap the MCP tool result envelope."""
    content = result.get("result", {}).get("content", [])
    if content:
        text = content[0].get("text", "{}")
        return json.loads(text)
    return {}


# ---------------------------------------------------------------------------
# Slash command setup
# ---------------------------------------------------------------------------

def setup_commands(
    bot: discord.ext.commands.Bot,
    gov_client: GovernanceClient,
    anima_client: AnimaClient,
) -> None:
    """Register slash commands on the bot's command tree."""

    tree = bot.tree

    @tree.command(name="status", description="Show current EISV for all agents")
    async def cmd_status(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            agents = await _fetch_agents(gov_client)
            metrics = await _fetch_metrics(gov_client, agents)
            embed = build_status_embed(agents, metrics)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/status error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Failed to fetch agent status."),
            )

    @tree.command(name="agent", description="Detailed view of one agent's metrics")
    @app_commands.describe(name="Agent ID or name")
    async def cmd_agent(interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()
        try:
            result = await gov_client.call_tool(
                "observe_agent", {"agent_id": name},
            )
            if not result:
                await interaction.followup.send(
                    embed=_error_embed(f"Agent '{name}' not found or governance unavailable."),
                )
                return
            data = _parse_tool_result(result)
            if isinstance(data, list):
                data = data[0] if data else {}
            agent_id = data.get("agent_id") or data.get("id") or name
            embed = build_agent_embed(agent_id, data)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/agent error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed(f"Failed to fetch agent '{name}'."),
            )

    @tree.command(name="health", description="System health check")
    async def cmd_health(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            health = await gov_client.fetch_health()
            if not health:
                await interaction.followup.send(
                    embed=_error_embed("Governance service unreachable."),
                )
                return
            embed = build_health_embed(health)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/health error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Health check failed."),
            )

    @tree.command(name="resume", description="Resume a paused agent")
    @app_commands.describe(agent="Agent ID to resume")
    async def cmd_resume(interaction: discord.Interaction, agent: str) -> None:
        await interaction.response.defer()
        try:
            result = await gov_client.call_tool(
                "operator_resume_agent", {"agent_id": agent},
            )
            if not result:
                await interaction.followup.send(
                    embed=_error_embed(f"Failed to resume agent '{agent}'."),
                )
                return
            data = _parse_tool_result(result)
            if isinstance(data, list):
                data = data[0] if data else {}
            embed = build_resume_embed(agent, data)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/resume error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed(f"Resume failed for '{agent}'."),
            )

    @tree.command(name="lumen", description="Current Lumen state and sensors")
    async def cmd_lumen(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            state = await anima_client.fetch_state()
            if not state:
                await interaction.followup.send(
                    embed=_error_embed("Lumen is offline."),
                )
                return
            embed = build_lumen_embed(state)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/lumen error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Failed to fetch Lumen state."),
            )


def _error_embed(message: str) -> discord.Embed:
    """Build a red error embed."""
    return discord.Embed(
        title="Error",
        description=message,
        colour=discord.Colour.red(),
    )


# ---------------------------------------------------------------------------
# Internal helpers (shared with HUDUpdater pattern)
# ---------------------------------------------------------------------------

async def _fetch_agents(gov_client: GovernanceClient) -> list[dict]:
    """Call list_agents and normalise response."""
    result = await gov_client.call_tool("list_agents", {})
    if result is None:
        return []
    try:
        data = _parse_tool_result(result)
        agents = []
        items = data if isinstance(data, list) else [data]
        for item in items:
            agent_id = item.get("agent_id") or item.get("id", "")
            label = item.get("label") or item.get("name") or agent_id
            agents.append({"id": agent_id, "label": label})
        return agents
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        log.warning("Failed to parse list_agents: %s", exc)
        return []


async def _fetch_metrics(
    gov_client: GovernanceClient, agents: list[dict],
) -> dict[str, dict]:
    """Fetch EISV metrics for each agent."""
    metrics: dict[str, dict] = {}
    for agent in agents:
        agent_id = agent["id"]
        result = await gov_client.call_tool(
            "get_governance_metrics", {"agent_id": agent_id},
        )
        if result is None:
            continue
        try:
            data = _parse_tool_result(result)
            if isinstance(data, list):
                data = data[0] if data else {}
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
